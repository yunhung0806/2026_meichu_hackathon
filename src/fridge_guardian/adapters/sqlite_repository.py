from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fridge_guardian.domain import (
    FaceTemplate,
    InteractionEvent,
    Item,
    ItemTemplate,
    User,
    utc_now,
)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS face_templates (
    template_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    feature BLOB NOT NULL,
    feature_kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    item_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES users(user_id),
    label TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS item_templates (
    template_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    feature BLOB NOT NULL,
    feature_kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS item_shares (
    item_id TEXT NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (item_id, user_id)
);
CREATE TABLE IF NOT EXISTS interaction_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    action TEXT NOT NULL,
    decision TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    user_id TEXT REFERENCES users(user_id),
    item_id TEXT REFERENCES items(item_id),
    identity_confidence REAL NOT NULL,
    identity_status TEXT NOT NULL DEFAULT 'UNKNOWN_USER',
    identity_second_score REAL NOT NULL DEFAULT 0,
    identity_margin REAL NOT NULL DEFAULT 0,
    identity_valid_frames INTEGER NOT NULL DEFAULT 0,
    identity_vote_ratio REAL NOT NULL DEFAULT 0,
    item_confidence REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_face_templates_user ON face_templates(user_id);
CREATE INDEX IF NOT EXISTS idx_item_templates_item ON item_templates(item_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON interaction_events(session_id);
"""


class SQLiteRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._migrate_interaction_events()
        self.connection.commit()

    def _migrate_interaction_events(self) -> None:
        """Add face diagnostics to databases created by the first MVP."""
        existing = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(interaction_events)")
        }
        additions = {
            "identity_status": "TEXT NOT NULL DEFAULT 'UNKNOWN_USER'",
            "identity_second_score": "REAL NOT NULL DEFAULT 0",
            "identity_margin": "REAL NOT NULL DEFAULT 0",
            "identity_valid_frames": "INTEGER NOT NULL DEFAULT 0",
            "identity_vote_ratio": "REAL NOT NULL DEFAULT 0",
        }
        for column, definition in additions.items():
            if column not in existing:
                self.connection.execute(
                    f"ALTER TABLE interaction_events ADD COLUMN {column} {definition}"
                )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SQLiteRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def add_user(self, display_name: str) -> User:
        user = User(str(uuid4()), display_name, utc_now())
        with self.connection:
            self.connection.execute(
                "INSERT INTO users(user_id, display_name, created_at) VALUES (?, ?, ?)",
                (user.user_id, user.display_name, user.created_at.isoformat()),
            )
        return user

    def list_users(self) -> list[User]:
        rows = self.connection.execute(
            "SELECT user_id, display_name, created_at FROM users ORDER BY created_at"
        ).fetchall()
        return [User(row["user_id"], row["display_name"], datetime.fromisoformat(row["created_at"])) for row in rows]

    def add_face_templates(
        self, user_id: str, features: Sequence[bytes], feature_kind: str
    ) -> None:
        created_at = utc_now().isoformat()
        with self.connection:
            self.connection.executemany(
                "INSERT INTO face_templates VALUES (?, ?, ?, ?, ?)",
                [
                    (str(uuid4()), user_id, sqlite3.Binary(feature), feature_kind, created_at)
                    for feature in features
                ],
            )

    def list_face_templates(self) -> list[FaceTemplate]:
        rows = self.connection.execute(
            "SELECT user_id, feature, feature_kind FROM face_templates"
        ).fetchall()
        return [FaceTemplate(row["user_id"], bytes(row["feature"]), row["feature_kind"]) for row in rows]

    def add_item(self, owner_id: str, label: str) -> Item:
        item = Item(str(uuid4()), owner_id, label, utc_now())
        with self.connection:
            self.connection.execute(
                "INSERT INTO items(item_id, owner_id, label, created_at) VALUES (?, ?, ?, ?)",
                (item.item_id, item.owner_id, item.label, item.created_at.isoformat()),
            )
        return item

    def get_item(self, item_id: str) -> Item | None:
        row = self.connection.execute(
            "SELECT item_id, owner_id, label, created_at FROM items WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return None
        return Item(row["item_id"], row["owner_id"], row["label"], datetime.fromisoformat(row["created_at"]))

    def list_item_templates(self) -> list[ItemTemplate]:
        rows = self.connection.execute(
            "SELECT item_id, feature, feature_kind FROM item_templates"
        ).fetchall()
        return [ItemTemplate(row["item_id"], bytes(row["feature"]), row["feature_kind"]) for row in rows]

    def add_item_templates(
        self, item_id: str, features: Sequence[bytes], feature_kind: str
    ) -> None:
        created_at = utc_now().isoformat()
        with self.connection:
            self.connection.executemany(
                "INSERT INTO item_templates VALUES (?, ?, ?, ?, ?)",
                [
                    (str(uuid4()), item_id, sqlite3.Binary(feature), feature_kind, created_at)
                    for feature in features
                ],
            )

    def share_item(self, item_id: str, user_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO item_shares VALUES (?, ?, ?)",
                (item_id, user_id, utc_now().isoformat()),
            )

    def is_shared_with(self, item_id: str, user_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM item_shares WHERE item_id = ? AND user_id = ?",
            (item_id, user_id),
        ).fetchone()
        return row is not None

    def record_event(self, event: InteractionEvent) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO interaction_events(
                    event_id, session_id, action, decision, occurred_at,
                    user_id, item_id, identity_confidence, identity_status,
                    identity_second_score, identity_margin, identity_valid_frames,
                    identity_vote_ratio, item_confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.session_id,
                    event.action.value,
                    event.decision.value,
                    event.occurred_at.isoformat(),
                    event.user_id,
                    event.item_id,
                    event.identity_confidence,
                    event.identity_status.value,
                    event.identity_second_score,
                    event.identity_margin,
                    event.identity_valid_frames,
                    event.identity_vote_ratio,
                    event.item_confidence,
                ),
            )
