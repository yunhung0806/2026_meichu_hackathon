"""Local backend for the identity-first fridge flow (no HTTP server required)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from secrets import token_urlsafe
from zoneinfo import ZoneInfo

from fridge_guardian.application.coordinator import SessionCoordinator
from fridge_guardian.domain import Action, Decision, DecisionCode, utc_now


@dataclass(frozen=True)
class Login:
    token: str
    user_id: str
    display_name: str
    expires_at: datetime


@dataclass(frozen=True)
class PutOptions:
    label: str
    shared: bool = False
    expires_on: date | None = None


@dataclass(frozen=True)
class OperationResult:
    decision: Decision
    warnings: tuple[str, ...] = ()


class FridgeService:
    """Single-process, single-thread UI service using the existing SQLite repository.

    Login tokens stay in memory; every physical operation rechecks the face.
    `clock` is injectable for deterministic date-boundary tests.
    """

    def __init__(self, coordinator: SessionCoordinator, *, timezone="Asia/Taipei", clock=utc_now):
        self.coordinator = coordinator
        self.repo = coordinator.repository
        self.clock = clock
        self.timezone = ZoneInfo(timezone)
        self.logins: dict[str, Login] = {}
        self.repo.connection.executescript("""
            CREATE TABLE IF NOT EXISTS inventory (
                item_id TEXT PRIMARY KEY REFERENCES items(item_id),
                shared INTEGER NOT NULL DEFAULT 0,
                put_at TEXT NOT NULL,
                expires_on TEXT,
                present INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS expiry_notices (
                item_id TEXT NOT NULL REFERENCES items(item_id),
                owner_id TEXT NOT NULL REFERENCES users(user_id),
                expires_on TEXT NOT NULL,
                acknowledged_at TEXT,
                PRIMARY KEY(item_id, expires_on)
            );
        """)

    def _today(self):
        return self.clock().astimezone(self.timezone).date()

    def identify(self, frames) -> Login:
        sid = self.coordinator._session_id(frames)
        result = self.coordinator.identity_provider.identify(sid, frames, self.repo.list_face_templates())
        user = next((u for u in self.repo.list_users() if u.user_id == result.user_id), None)
        if result.session_id != sid or user is None:
            raise PermissionError("Unknown user; enroll or retry")
        return self._issue_login(user)

    def enroll(self, display_name, frames) -> Login:
        """Enroll a new local face and immediately start its memory-only session."""
        user = self.coordinator.enroll_user(display_name, frames)
        return self._issue_login(user)

    def _issue_login(self, user) -> Login:
        now = self.clock()
        self.logins = {k: v for k, v in self.logins.items() if v.expires_at > now}
        login = Login(token_urlsafe(32), user.user_id, user.display_name, now + timedelta(minutes=5))
        self.logins[login.token] = login
        return login

    def _login(self, token):
        login = self.logins.get(token)
        if login is None or login.expires_at <= self.clock():
            self.logins.pop(token, None)
            raise PermissionError("Identify your face again")
        return login

    def logout(self, token):
        self.logins.pop(token, None)

    def inventory(self, token):
        user = self._login(token)
        return [dict(row) for row in self.repo.connection.execute("""
            SELECT i.item_id, i.label, i.owner_id, s.shared, s.put_at, s.expires_on
            FROM inventory s JOIN items i USING(item_id)
            WHERE i.owner_id = ? AND s.present = 1 ORDER BY s.put_at
        """, (user.user_id,))]

    def process(self, token, action, frames, options: PutOptions | None = None):
        login = self._login(token)
        action = Action(action)
        sid = self.coordinator._session_id(frames)
        identity = self.coordinator.identity_provider.identify(sid, frames, self.repo.list_face_templates())
        if identity.session_id != sid or identity.user_id != login.user_id:
            raise PermissionError("The person changed; identify again")
        if action is Action.PUT_IN:
            if options is None or not options.label.strip() or len(options.label) > 200:
                raise ValueError("PUT_IN requires a label of 1–200 characters")
            if type(options.shared) is not bool:
                raise ValueError("shared must be a boolean")
            if options.expires_on is not None and type(options.expires_on) is not date:
                raise ValueError("expires_on must be a date or None")
        recognizer = self.coordinator.item_recognizer
        match = recognizer.identify(sid, frames, self.repo.list_item_templates())
        if match.session_id != sid:
            raise ValueError("Item result belongs to another session")
        item = self.repo.get_item(match.item_id) if match.item_id else None
        state = self.repo.connection.execute("SELECT * FROM inventory WHERE item_id=?", (match.item_id,)).fetchone()
        warnings = []
        if action is Action.PUT_IN:
            if item is not None and item.owner_id != login.user_id:
                code, message = DecisionCode.WARN_NOT_OWNER, "Only the owner may return or edit this item"
            elif state is not None and state["present"]:
                code, message = DecisionCode.ITEM_ALREADY_REGISTERED, "Item is already in the fridge"
            else:
                features = recognizer.extract_templates(sid, frames) if item is None else []
                if item is None and len(features) < 3:
                    code, message = DecisionCode.UNKNOWN_ITEM, "Need three clear item views"
                else:
                    # Direct SQL keeps item, templates and inventory atomic; repository helpers commit individually.
                    from uuid import uuid4
                    now = self.clock().isoformat()
                    item_id = item.item_id if item else str(uuid4())
                    with self.repo.connection:
                        if item is None:
                            self.repo.connection.execute("INSERT INTO items VALUES (?, ?, ?, ?)", (item_id, login.user_id, options.label.strip(), now))
                            kind = getattr(recognizer, "feature_kind", "spatial-hsv-f32-v1")
                            self.repo.connection.executemany("INSERT INTO item_templates VALUES (?, ?, ?, ?, ?)", [(str(uuid4()), item_id, f, kind, now) for f in features])
                        else:
                            self.repo.connection.execute("UPDATE items SET label=? WHERE item_id=?", (options.label.strip(), item_id))
                        self.repo.connection.execute("""INSERT INTO inventory VALUES (?, ?, ?, ?, 1)
                            ON CONFLICT(item_id) DO UPDATE SET shared=excluded.shared,
                            put_at=excluded.put_at, expires_on=excluded.expires_on, present=1""",
                            (item_id, int(options.shared), now, options.expires_on.isoformat() if options.expires_on else None))
                    item = self.repo.get_item(item_id)
                    code, message = DecisionCode.ITEM_REGISTERED, f"Stored {item.label} for {login.display_name}"
        elif item is None or state is None or not state["present"]:
            code, message = DecisionCode.UNKNOWN_ITEM, "Item is not in the tracked inventory"
        else:
            allowed = item.owner_id == login.user_id or bool(state["shared"]) or self.repo.is_shared_with(item.item_id, login.user_id)
            code = (DecisionCode.ALLOW_OWNER if item.owner_id == login.user_id else DecisionCode.ALLOW_SHARED) if allowed else DecisionCode.WARN_NOT_OWNER
            message = "Take-out recorded" if allowed else "This item belongs to another user"
            if state["expires_on"] and date.fromisoformat(state["expires_on"]) < self._today():
                warnings.append("EXPIRED: recorded package date has passed")
            if allowed:
                with self.repo.connection:
                    self.repo.connection.execute("UPDATE inventory SET present=0 WHERE item_id=?", (item.item_id,))
        decision = Decision(
            session_id=sid,
            action=action,
            code=code,
            message=message + ("; " + "; ".join(warnings) if warnings else ""),
            user_id=login.user_id,
            item_id=item.item_id if item else None,
            identity_confidence=identity.confidence,
            identity_status=identity.status,
            identity_second_score=identity.second_score,
            identity_margin=identity.margin,
            identity_valid_frames=identity.valid_frames,
            identity_vote_ratio=identity.vote_ratio,
            item_confidence=match.confidence,
            warnings=tuple(warnings),
        )
        self.coordinator._finish(decision)
        return OperationResult(decision, tuple(warnings))

    def refresh_reminders(self):
        """Call at startup and periodically (e.g. every 60s); persisted, idempotent inbox."""
        tomorrow = (self._today() + timedelta(days=1)).isoformat()
        with self.repo.connection:
            self.repo.connection.execute("""INSERT OR IGNORE INTO expiry_notices(item_id, owner_id, expires_on)
                SELECT i.item_id, i.owner_id, s.expires_on FROM inventory s JOIN items i USING(item_id)
                WHERE s.present=1 AND s.expires_on=?""", (tomorrow,))

    def reminders(self, token):
        login = self._login(token)
        self.refresh_reminders()
        return [dict(r) for r in self.repo.connection.execute("""SELECT n.item_id, i.label, n.expires_on
            FROM expiry_notices n JOIN items i USING(item_id) JOIN inventory s USING(item_id)
            WHERE n.owner_id=? AND n.acknowledged_at IS NULL AND s.present=1
            AND s.expires_on=n.expires_on""", (login.user_id,))]

    def acknowledge_reminder(self, token, item_id, expires_on):
        login = self._login(token)
        with self.repo.connection:
            self.repo.connection.execute("""UPDATE expiry_notices SET acknowledged_at=?
                WHERE owner_id=? AND item_id=? AND expires_on=?""",
                (self.clock().isoformat(), login.user_id, item_id, expires_on))
