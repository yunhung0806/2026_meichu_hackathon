from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fridge_guardian.adapters.sqlite_repository import SQLiteRepository
from fridge_guardian.domain import (
    Action,
    DecisionCode,
    IdentityStatus,
    InteractionEvent,
    utc_now,
)


class SQLitePersistenceTests(unittest.TestCase):
    def test_create_write_close_and_reopen(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "guardian.db"
            first = SQLiteRepository(path)
            owner = first.add_user("Owner")
            guest = first.add_user("Guest")
            first.add_face_templates(owner.user_id, [b"face"], "test-face")
            item = first.add_item(owner.user_id, "milk")
            first.add_item_templates(item.item_id, [b"item"], "test-item")
            first.share_item(item.item_id, guest.user_id)
            first.record_event(
                InteractionEvent(
                    event_id=str(uuid4()),
                    session_id="session-persisted",
                    action=Action.TAKE_OUT,
                    decision=DecisionCode.ALLOW_SHARED,
                    occurred_at=utc_now(),
                    user_id=guest.user_id,
                    item_id=item.item_id,
                    identity_confidence=0.91,
                    identity_status=IdentityStatus.MATCHED,
                    identity_second_score=0.22,
                    identity_margin=0.69,
                    identity_valid_frames=12,
                    identity_vote_ratio=0.92,
                    item_confidence=0.92,
                )
            )
            first.close()

            reopened = SQLiteRepository(path)
            try:
                self.assertEqual(len(reopened.list_users()), 2)
                self.assertEqual(reopened.list_face_templates()[0].feature, b"face")
                self.assertEqual(reopened.get_item(item.item_id).owner_id, owner.user_id)
                self.assertEqual(reopened.list_item_templates()[0].feature, b"item")
                self.assertTrue(reopened.is_shared_with(item.item_id, guest.user_id))
                count = reopened.connection.execute(
                    "SELECT COUNT(*) FROM interaction_events WHERE session_id = ?",
                    ("session-persisted",),
                ).fetchone()[0]
                self.assertEqual(count, 1)
                diagnostics = reopened.connection.execute(
                    """
                    SELECT identity_status, identity_second_score, identity_margin,
                           identity_valid_frames, identity_vote_ratio
                    FROM interaction_events WHERE session_id = ?
                    """,
                    ("session-persisted",),
                ).fetchone()
                self.assertEqual(diagnostics["identity_status"], "MATCHED")
                self.assertEqual(diagnostics["identity_valid_frames"], 12)
                self.assertAlmostEqual(diagnostics["identity_margin"], 0.69)
            finally:
                reopened.close()

    def test_migrates_first_mvp_event_table_without_data_loss(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE interaction_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    user_id TEXT,
                    item_id TEXT,
                    identity_confidence REAL NOT NULL,
                    item_confidence REAL NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO interaction_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("old", "old-session", "TAKE_OUT", "UNKNOWN_USER", utc_now().isoformat(), None, None, 0.0, 0.0),
            )
            connection.commit()
            connection.close()

            repository = SQLiteRepository(path)
            try:
                columns = {
                    row["name"]
                    for row in repository.connection.execute(
                        "PRAGMA table_info(interaction_events)"
                    )
                }
                self.assertIn("identity_status", columns)
                self.assertIn("identity_valid_frames", columns)
                count = repository.connection.execute(
                    "SELECT COUNT(*) FROM interaction_events WHERE event_id = 'old'"
                ).fetchone()[0]
                self.assertEqual(count, 1)
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()
