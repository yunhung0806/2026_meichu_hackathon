from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fridge_guardian.adapters.sqlite_repository import SQLiteRepository
from fridge_guardian.domain import Action, DecisionCode, InteractionEvent, utc_now


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
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
