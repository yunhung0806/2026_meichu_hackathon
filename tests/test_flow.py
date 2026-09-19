from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fridge_guardian.adapters.sqlite_repository import SQLiteRepository
from fridge_guardian.application import SessionCoordinator
from fridge_guardian.domain import Action, DecisionCode, FrameSample, utc_now
from tests.fakes import FakeIdentityProvider, FakeItemRecognizer, RecordingFeedback


def frames(session_id: str = "session-1"):
    return [FrameSample(session_id, utc_now(), object()) for _ in range(4)]


class MockFlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = SQLiteRepository(Path(self.tempdir.name) / "test.db")
        self.identity = FakeIdentityProvider()
        self.items = FakeItemRecognizer()
        self.feedback = RecordingFeedback()
        self.coordinator = SessionCoordinator(self.repo, self.identity, self.items, self.feedback)

    def tearDown(self):
        self.repo.close()
        self.tempdir.cleanup()

    def test_put_in_then_owner_and_non_owner_take_out(self):
        user_a = self.repo.add_user("A")
        user_b = self.repo.add_user("B")
        self.identity.user_id = user_a.user_id

        put = self.coordinator.process(Action.PUT_IN, frames("put-session"))
        self.assertEqual(put.code, DecisionCode.ITEM_REGISTERED)
        self.assertIsNotNone(put.item_id)

        self.items.item_id = put.item_id
        self.items.confidence = 0.94
        owner_take = self.coordinator.process(Action.TAKE_OUT, frames("owner-session"))
        self.assertEqual(owner_take.code, DecisionCode.ALLOW_OWNER)

        self.identity.user_id = user_b.user_id
        non_owner_take = self.coordinator.process(Action.TAKE_OUT, frames("other-session"))
        self.assertEqual(non_owner_take.code, DecisionCode.WARN_NOT_OWNER)

    def test_unknown_user_stops_before_item_decision(self):
        self.identity.user_id = None
        result = self.coordinator.process(Action.TAKE_OUT, frames())
        self.assertEqual(result.code, DecisionCode.UNKNOWN_USER)
        self.assertIsNone(result.item_id)

    def test_unknown_item_does_not_guess(self):
        user = self.repo.add_user("A")
        self.identity.user_id = user.user_id
        self.items.item_id = None
        self.items.confidence = 0.41
        result = self.coordinator.process(Action.TAKE_OUT, frames())
        self.assertEqual(result.code, DecisionCode.UNKNOWN_ITEM)
        self.assertEqual(result.item_confidence, 0.41)

    def test_mixed_session_frames_are_rejected(self):
        mixed = frames("one") + frames("two")
        with self.assertRaises(ValueError):
            self.coordinator.process(Action.TAKE_OUT, mixed)


if __name__ == "__main__":
    unittest.main()
