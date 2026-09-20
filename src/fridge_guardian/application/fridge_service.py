"""Local backend for the identity-first fridge flow (no HTTP server required)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from secrets import token_urlsafe
from uuid import uuid4
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

    def validate_new_user_name(self, display_name: str) -> str:
        """Validate a local display name before the camera capture begins."""
        return self.coordinator.validate_new_user_name(display_name)

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
        """Return every present record, with permissions for the signed-in viewer."""
        user = self._login(token)
        return self._inventory_rows(user.user_id, authorized_only=False)

    def accessible_inventory(self, token):
        """Return only food the viewer may use; recipes/RAG intentionally use this."""
        user = self._login(token)
        return self._inventory_rows(user.user_id, authorized_only=True)

    def _inventory_rows(self, user_id: str, *, authorized_only: bool):
        authorization_filter = """AND (
            i.owner_id=:user_id OR s.shared=1 OR EXISTS (
              SELECT 1 FROM item_shares granted
              WHERE granted.item_id=i.item_id AND granted.user_id=:user_id
            )
        )""" if authorized_only else ""
        rows = self.repo.connection.execute(f"""
            SELECT i.item_id, i.label, i.owner_id,
                   u.display_name AS owner_display_name,
                   u.display_name AS owner_name,
                   s.shared AS shared,
                   CASE
                     WHEN i.owner_id=:user_id THEN 'OWNER'
                     WHEN s.shared=1 THEN 'SHARED_ALL'
                     WHEN EXISTS (
                       SELECT 1 FROM item_shares direct_share
                       WHERE direct_share.item_id=i.item_id
                         AND direct_share.user_id=:user_id
                     ) THEN 'SHARED_DIRECT'
                     ELSE 'PRIVATE_VISIBLE'
                   END AS access_type,
                   CASE WHEN i.owner_id=:user_id THEN 1 ELSE 0 END AS can_edit,
                   CASE WHEN i.owner_id=:user_id OR s.shared=1 OR EXISTS (
                     SELECT 1 FROM item_shares take_share
                     WHERE take_share.item_id=i.item_id
                       AND take_share.user_id=:user_id
                   ) THEN 1 ELSE 0 END AS can_take,
                   s.put_at, s.expires_on
            FROM inventory s
            JOIN items i USING(item_id)
            JOIN users u ON u.user_id=i.owner_id
            WHERE s.present=1 {authorization_filter}
            ORDER BY (s.expires_on IS NULL), s.expires_on, s.put_at, i.item_id
        """, {"user_id": user_id}).fetchall()
        result = []
        for row in rows:
            public = dict(row)
            public["shared"] = bool(public["shared"])
            public["can_edit"] = bool(public["can_edit"])
            public["can_take"] = bool(public["can_take"])
            if public["can_edit"]:
                recipients = self.repo.connection.execute(
                    """SELECT share.user_id, user.display_name
                       FROM item_shares share
                       JOIN users user ON user.user_id=share.user_id
                       WHERE share.item_id=?
                       ORDER BY user.created_at, user.user_id""",
                    (public["item_id"],),
                ).fetchall()
                public["shared_user_ids"] = [entry["user_id"] for entry in recipients]
                public["shared_user_names"] = [entry["display_name"] for entry in recipients]
            else:
                public["shared_user_ids"] = []
                public["shared_user_names"] = []
            result.append(public)
        return result

    def update_inventory(self, token, item_id: str, changes: dict[str, object]):
        """Update owner-controlled metadata without changing item identity or presence."""
        login = self._login(token)
        allowed = {"label", "expires_on", "shared", "shared_user_ids"}
        if not changes or set(changes) - allowed:
            raise ValueError("Update label, expires_on, shared, or shared_user_ids only")
        item = self.repo.get_item(item_id)
        state = self.repo.connection.execute(
            "SELECT * FROM inventory WHERE item_id=?", (item_id,)
        ).fetchone()
        if item is None or state is None:
            raise KeyError("Inventory item was not found")
        if item.owner_id != login.user_id:
            raise PermissionError("Only the owner may edit this inventory item")
        if not state["present"]:
            raise RuntimeError("Only present inventory items may be edited")

        label = changes.get("label")
        if "label" in changes:
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 200:
                raise ValueError("label must contain 1–200 characters")
            label = label.strip()
        expires_on = changes.get("expires_on")
        if "expires_on" in changes and expires_on is not None and type(expires_on) is not date:
            raise ValueError("expires_on must be a date or null")
        shared = changes.get("shared")
        if "shared" in changes and type(shared) is not bool:
            raise ValueError("shared must be a boolean")
        shared_user_ids = changes.get("shared_user_ids", [])
        if "shared_user_ids" in changes:
            if not isinstance(shared_user_ids, list) or any(
                not isinstance(value, str) for value in shared_user_ids
            ):
                raise ValueError("shared_user_ids must be a list of user IDs")
            shared_user_ids = list(dict.fromkeys(shared_user_ids))
            if login.user_id in shared_user_ids:
                raise ValueError("The owner cannot be a share recipient")
            known_users = {user.user_id for user in self.repo.list_users()}
            if any(value not in known_users for value in shared_user_ids):
                raise ValueError("One or more selected sharing users are invalid")
        if shared is True and shared_user_ids:
            raise ValueError("Choose all-user sharing or selected users, not both")

        sharing_changed = "shared" in changes or "shared_user_ids" in changes
        effective_shared = bool(shared) if "shared" in changes else False
        event_id = str(uuid4())
        session_id = f"inventory-edit:{uuid4()}"
        occurred_at = self.clock().isoformat()

        with self.repo.connection:
            if "label" in changes:
                self.repo.connection.execute(
                    "UPDATE items SET label=? WHERE item_id=?", (label, item_id)
                )
            if "expires_on" in changes:
                self.repo.connection.execute(
                    "UPDATE inventory SET expires_on=? WHERE item_id=?",
                    (expires_on.isoformat() if expires_on else None, item_id),
                )
            if sharing_changed:
                self.repo.connection.execute(
                    "UPDATE inventory SET shared=? WHERE item_id=?",
                    (int(effective_shared), item_id),
                )
                self.repo.connection.execute(
                    "DELETE FROM item_shares WHERE item_id=?", (item_id,)
                )
                self.repo.connection.executemany(
                    "INSERT INTO item_shares(item_id, user_id, created_at) VALUES (?, ?, ?)",
                    [(item_id, user_id, occurred_at) for user_id in shared_user_ids],
                )
            self.repo.connection.execute(
                """INSERT INTO interaction_events(
                       event_id, session_id, action, decision, occurred_at,
                       user_id, item_id, identity_confidence, identity_status,
                       identity_second_score, identity_margin, identity_valid_frames,
                       identity_vote_ratio, item_confidence
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'UNKNOWN_USER', 0, 0, 0, 0, 0)""",
                (
                    event_id, session_id, Action.INVENTORY_EDIT.value,
                    DecisionCode.ITEM_UPDATED.value, occurred_at,
                    login.user_id, item_id,
                ),
            )
        return next(
            row for row in self._inventory_rows(login.user_id, authorized_only=False)
            if row["item_id"] == item_id
        )

    def members(self, token):
        """List registered users that the signed-in user may share items with."""
        user = self._login(token)
        return [
            {"user_id": member.user_id, "display_name": member.display_name}
            for member in self.repo.list_users()
            if member.user_id != user.user_id
        ]

    def history(self, token, *, limit=50):
        """Return only the signed-in user's recent local interaction events."""
        user = self._login(token)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("history limit must be between 1 and 100")
        return [dict(row) for row in self.repo.connection.execute(
            """SELECT e.event_id, e.session_id, e.action, e.decision,
                      e.occurred_at, e.item_id, i.label AS item_label,
                      owner.display_name AS owner_name,
                      CASE WHEN i.owner_id=e.user_id THEN 'OWNER' ELSE 'ACTOR' END AS viewer_role,
                      CASE
                        WHEN e.decision='WARN_NOT_OWNER' AND i.owner_id=e.user_id THEN (
                          SELECT actor.display_name
                          FROM interaction_events attempt
                          JOIN users actor ON actor.user_id=attempt.user_id
                          WHERE attempt.session_id=e.session_id
                            AND attempt.item_id=e.item_id
                            AND attempt.decision='WARN_NOT_OWNER'
                            AND attempt.user_id<>i.owner_id
                          LIMIT 1
                        )
                        ELSE owner.display_name
                      END AS related_user_name
               FROM interaction_events e
               LEFT JOIN items i ON i.item_id=e.item_id
               LEFT JOIN users owner ON owner.user_id=i.owner_id
               WHERE e.user_id=?
               ORDER BY e.occurred_at DESC
               LIMIT ?""",
            (user.user_id, limit),
        )]

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
