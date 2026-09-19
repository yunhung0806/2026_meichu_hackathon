from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np

from fridge_guardian.adapters.item_vision import EMBEDDING_KIND, ROI_EMBEDDING_KIND
from fridge_guardian.adapters.sqlite_repository import SQLiteRepository
from fridge_guardian.application import SessionCoordinator
from fridge_guardian.application.fridge_service import FridgeService
from fridge_guardian.application.item_inspection import InspectionError, ItemInspectionManager
from fridge_guardian.domain import Action, DecisionCode, FrameSample
from tests.fakes import FakeIdentityProvider, FakeItemRecognizer, RecordingFeedback


class Clock:
    def __init__(self): self.now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    def __call__(self): return self.now


class Vision:
    def __init__(self):
        self.status = "NO_MATCH"
        self.category_status = "OK"
        self.localization_status = "OK"
        self.candidates = []
        self.gallery = None

    def infer(self, roi, gallery, *, request_id):
        self.gallery = gallery
        values = [0.0] * 384; values[0] = 1.0
        match = {
            "status": self.status, "candidates": list(self.candidates),
            "first_item_id": self.candidates[0]["item_id"] if self.candidates else None,
            "first_similarity": self.candidates[0]["similarity"] if self.candidates else 0.0,
            "second_item_id": None, "second_similarity": 0.0, "margin": 0.1,
        }
        return {
            "localization": {"status": self.localization_status, "score": 0.9, "box": [1, 1, 20, 20]},
            "category": {"status": self.category_status, "top3": [{"label": "beverage", "score": 0.6}]},
            "instance": match,
            "embeddings": None if self.localization_status != "OK" else {"crop": values, "roi": values},
            "latency_ms": {"total": 6.0},
        }


class ItemInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = SQLiteRepository(Path(self.temp.name) / "test.db")
        self.identity = FakeIdentityProvider()
        self.feedback = RecordingFeedback()
        coordinator = SessionCoordinator(self.repo, self.identity, FakeItemRecognizer(), self.feedback)
        self.clock = Clock()
        self.service = FridgeService(coordinator, clock=self.clock)
        self.owner = self.repo.add_user("Owner")
        self.other = self.repo.add_user("Other")
        self.identity.user_id = self.owner.user_id
        self.login = self.service._issue_login(self.owner)
        self.vision = Vision()
        self.manager = ItemInspectionManager(self.service, self.vision, ttl_seconds=120)

    def tearDown(self):
        self.repo.close(); self.temp.cleanup()

    def frames(self):
        session_id = str(uuid4())
        image = np.full((100, 160, 3), 100, dtype=np.uint8)
        return [FrameSample(session_id, self.clock(), image.copy()) for _ in range(3)]

    def add_present(self, owner=None, *, label="milk", shared=False):
        user = owner or self.owner
        item = self.repo.add_item(user.user_id, label)
        vector = np.zeros(384, dtype=np.float32); vector[0] = 1.0
        self.repo.add_item_templates(item.item_id, [vector.tobytes()], EMBEDDING_KIND)
        self.repo.add_item_templates(item.item_id, [vector.tobytes()], ROI_EMBEDDING_KIND)
        with self.repo.connection:
            self.repo.connection.execute(
                "INSERT INTO inventory VALUES (?, ?, ?, ?, 1)",
                (item.item_id, int(shared), self.clock().isoformat(), None),
            )
        return item

    def inspect(self, action=Action.PUT_IN):
        return self.manager.inspect(self.login.token, action, self.frames())

    def commit(self, inspection, **overrides):
        values = dict(
            token=self.login.token, inspection_id=inspection["inspection_id"],
            action=inspection["action"], confirmed=True, label="reviewed milk",
            selected_item_id=None, add_as_new=True, shared=False, expires_on=None,
        )
        values.update(overrides)
        return self.manager.commit(**values)

    def test_inspection_does_not_mutate_and_unknown_category_keeps_embedding(self):
        self.vision.category_status = "UNKNOWN_CATEGORY"
        before = self.repo.connection.total_changes
        result = self.inspect()
        self.assertEqual(result["category"]["status"], "UNKNOWN_CATEGORY")
        self.assertTrue(result["committable"])
        self.assertEqual(self.repo.connection.total_changes, before)
        pending = self.manager.pending[result["inspection_id"]]
        self.assertEqual(len(pending.crop_embedding), 384 * 4)

    def test_put_no_match_creates_reviewed_item_and_both_embeddings(self):
        inspection = self.inspect()
        result = self.commit(inspection, label="  corrected label  ")
        item = self.repo.get_item(result.decision.item_id)
        self.assertEqual(item.label, "corrected label")
        kinds = {template.feature_kind for template in self.repo.list_item_templates()}
        self.assertEqual(kinds, {EMBEDDING_KIND, ROI_EMBEDDING_KIND})

    def test_put_can_share_with_selected_user_only(self):
        stranger = self.repo.add_user("Stranger")
        inspection = self.inspect()
        result = self.commit(
            inspection,
            shared_user_ids=[self.other.user_id],
        )
        self.assertTrue(
            self.repo.is_shared_with(result.decision.item_id, self.other.user_id)
        )
        self.assertFalse(
            self.repo.is_shared_with(result.decision.item_id, stranger.user_id)
        )
        recipient = self.service._issue_login(self.other)
        outsider = self.service._issue_login(stranger)
        self.assertEqual(
            [row["item_id"] for row in self.service.inventory(recipient.token)],
            [result.decision.item_id],
        )
        outsider_inventory = self.service.inventory(outsider.token)
        self.assertEqual(
            [row["item_id"] for row in outsider_inventory],
            [result.decision.item_id],
        )
        self.assertFalse(outsider_inventory[0]["can_take"])
        self.assertEqual(self.service.accessible_inventory(outsider.token), [])

    def test_put_rejects_injected_share_user(self):
        inspection = self.inspect()
        with self.assertRaisesRegex(InspectionError, "sharing users are invalid"):
            self.commit(inspection, shared_user_ids=["injected-user"])

    def test_expired_and_completed_inspections_cannot_commit(self):
        expired = self.inspect()
        self.clock.now += timedelta(seconds=121)
        with self.assertRaisesRegex(InspectionError, "expired"):
            self.commit(expired)
        fresh = self.inspect()
        self.commit(fresh)
        with self.assertRaisesRegex(InspectionError, "already committed"):
            self.commit(fresh)

    def test_ambiguous_requires_offered_existing_or_explicit_new(self):
        item = self.add_present()
        with self.repo.connection:
            self.repo.connection.execute("UPDATE inventory SET present=0 WHERE item_id=?", (item.item_id,))
        self.vision.status = "AMBIGUOUS"
        self.vision.candidates = [{"item_id": item.item_id, "similarity": 0.91}]
        inspection = self.inspect()
        with self.assertRaisesRegex(InspectionError, "Choose a stored item"):
            self.commit(inspection, add_as_new=False)
        with self.assertRaisesRegex(InspectionError, "not offered"):
            self.commit(inspection, add_as_new=False, selected_item_id="injected")

    def test_edited_label_does_not_change_selected_item_identity(self):
        item = self.add_present()
        with self.repo.connection:
            self.repo.connection.execute("UPDATE inventory SET present=0 WHERE item_id=?", (item.item_id,))
        self.vision.status = "MATCHED"
        self.vision.candidates = [{"item_id": item.item_id, "similarity": 0.93}]
        inspection = self.inspect()
        result = self.commit(
            inspection, label="new display name", add_as_new=False,
            selected_item_id=item.item_id,
        )
        self.assertEqual(result.decision.item_id, item.item_id)
        self.assertEqual(self.repo.get_item(item.item_id).label, "new display name")

    def test_matched_put_defaults_to_best_eligible_candidate(self):
        item = self.add_present(label="original")
        with self.repo.connection:
            self.repo.connection.execute("UPDATE inventory SET present=0 WHERE item_id=?", (item.item_id,))
        self.vision.status = "MATCHED"
        self.vision.candidates = [{"item_id": item.item_id, "similarity": 0.93}]
        inspection = self.inspect()
        result = self.commit(
            inspection, add_as_new=False, selected_item_id=None, label="reviewed",
        )
        self.assertEqual(result.decision.item_id, item.item_id)

    def test_matched_put_can_add_new_without_mutating_matched_item(self):
        item = self.add_present(label="original")
        with self.repo.connection:
            self.repo.connection.execute("UPDATE inventory SET present=0 WHERE item_id=?", (item.item_id,))
        self.vision.status = "MATCHED"
        self.vision.candidates = [{"item_id": item.item_id, "similarity": 0.93}]
        inspection = self.inspect()
        result = self.commit(
            inspection, add_as_new=True, selected_item_id=None, label="different item",
        )
        self.assertNotEqual(result.decision.item_id, item.item_id)
        self.assertEqual(self.repo.get_item(item.item_id).label, "original")
        self.assertEqual(self.repo.get_item(result.decision.item_id).label, "different item")
        states = self.repo.connection.execute(
            "SELECT item_id, present FROM inventory WHERE item_id IN (?, ?)",
            (item.item_id, result.decision.item_id),
        ).fetchall()
        self.assertEqual(
            {row["item_id"]: row["present"] for row in states},
            {item.item_id: 0, result.decision.item_id: 1},
        )

    def test_matched_put_without_eligible_candidate_can_add_new(self):
        ineligible = self.add_present(label="already present")
        self.vision.status = "MATCHED"
        self.vision.candidates = [{"item_id": ineligible.item_id, "similarity": 0.93}]
        inspection = self.inspect()
        self.assertEqual(inspection["instance"]["candidates"], [])
        result = self.commit(
            inspection, add_as_new=True, selected_item_id=None, label="new physical item",
        )
        self.assertNotEqual(result.decision.item_id, ineligible.item_id)
        present = self.repo.connection.execute(
            "SELECT COUNT(*) FROM inventory WHERE present=1 AND item_id IN (?, ?)",
            (ineligible.item_id, result.decision.item_id),
        ).fetchone()[0]
        self.assertEqual(present, 2)

    def test_backend_preserves_sidecar_candidate_order(self):
        first = self.add_present(label="first")
        second = self.add_present(label="second")
        with self.repo.connection:
            self.repo.connection.execute("UPDATE inventory SET present=0")
        self.vision.status = "AMBIGUOUS"
        self.vision.candidates = [
            {"item_id": second.item_id, "similarity": 0.89},
            {"item_id": first.item_id, "similarity": 0.88},
        ]
        result = self.inspect()
        self.assertEqual([entry["item_id"] for entry in result["instance"]["candidates"]], [second.item_id, first.item_id])

    def test_take_no_match_exposes_only_authorized_present_inventory(self):
        own = self.add_present(label="own")
        shared = self.add_present(owner=self.other, label="shared", shared=True)
        private = self.add_present(owner=self.other, label="private", shared=False)
        result = self.inspect(Action.TAKE_OUT)
        ids = {item["item_id"] for item in result["authorized_inventory"]}
        self.assertEqual(ids, {own.item_id, shared.item_id})
        self.assertNotIn(private.item_id, ids)
        taken = self.commit(
            result, action="TAKE_OUT", label=None, add_as_new=False,
            selected_item_id=shared.item_id,
        )
        self.assertEqual(taken.decision.item_id, shared.item_id)

    def test_take_matched_private_item_warns_without_mutation(self):
        own = self.add_present(label="authorized alternative")
        private = self.add_present(owner=self.other, label="private", shared=False)
        self.vision.status = "MATCHED"
        self.vision.candidates = [{"item_id": private.item_id, "similarity": 0.94}]
        result = self.inspect(Action.TAKE_OUT)

        self.assertEqual(result["review_state"], "WARN_NOT_OWNER")
        self.assertEqual(result["review_decision"], "WARN_NOT_OWNER")
        self.assertEqual(result["instance"]["candidates"], [])
        self.assertEqual(
            {item["item_id"] for item in result["authorized_inventory"]},
            {own.item_id},
        )
        self.assertTrue(result["committable"])
        self.assertEqual(self.feedback.decisions[-1].code.value, "WARN_NOT_OWNER")
        event_users = {
            row["user_id"]
            for row in self.repo.connection.execute(
                "SELECT user_id FROM interaction_events WHERE session_id=?",
                (self.manager.pending[result["inspection_id"]].session_id,),
            )
        }
        self.assertEqual(event_users, {self.owner.user_id, self.other.user_id})
        state = self.repo.connection.execute(
            "SELECT present FROM inventory WHERE item_id=?", (private.item_id,)
        ).fetchone()
        self.assertEqual(state["present"], 1)

    def test_take_ambiguous_rejects_unauthorized_candidate(self):
        private = self.add_present(owner=self.other, label="private")
        self.vision.status = "AMBIGUOUS"
        self.vision.candidates = [{"item_id": private.item_id, "similarity": 0.9}]
        result = self.inspect(Action.TAKE_OUT)
        self.assertEqual(result["instance"]["candidates"], [])
        self.assertEqual(result["review_state"], "WARN_NOT_OWNER")
        self.assertEqual(result["review_decision"], "WARN_NOT_OWNER")
        self.assertEqual(self.feedback.decisions[-1].code, DecisionCode.WARN_NOT_OWNER)
        event_count = self.repo.connection.execute(
            "SELECT COUNT(*) FROM interaction_events WHERE session_id=?",
            (self.manager.pending[result["inspection_id"]].session_id,),
        ).fetchone()[0]
        self.assertEqual(event_count, 2)
        with self.assertRaisesRegex(InspectionError, "not authorized"):
            self.commit(result, action="TAKE_OUT", label=None, add_as_new=False, selected_item_id=private.item_id)

    def test_take_ambiguous_requires_and_accepts_authorized_selection(self):
        own = self.add_present(label="own")
        self.vision.status = "AMBIGUOUS"
        self.vision.candidates = [{"item_id": own.item_id, "similarity": 0.9}]
        result = self.inspect(Action.TAKE_OUT)
        with self.assertRaisesRegex(InspectionError, "Choose one"):
            self.commit(result, action="TAKE_OUT", label=None, add_as_new=False)
        committed = self.commit(
            result, action="TAKE_OUT", label=None, add_as_new=False,
            selected_item_id=own.item_id,
        )
        self.assertEqual(committed.decision.item_id, own.item_id)

    def test_take_matched_can_be_corrected_to_another_authorized_item(self):
        matched = self.add_present(label="AI match")
        corrected = self.add_present(label="actual item")
        self.vision.status = "MATCHED"
        self.vision.candidates = [{"item_id": matched.item_id, "similarity": 0.94}]
        inspection = self.inspect(Action.TAKE_OUT)
        self.assertEqual(
            {item["item_id"] for item in inspection["authorized_inventory"]},
            {matched.item_id, corrected.item_id},
        )
        result = self.commit(
            inspection, action="TAKE_OUT", label=None, add_as_new=False,
            selected_item_id=corrected.item_id,
        )
        self.assertEqual(result.decision.item_id, corrected.item_id)
        states = dict(self.repo.connection.execute(
            "SELECT item_id, present FROM inventory WHERE item_id IN (?, ?)",
            (matched.item_id, corrected.item_id),
        ).fetchall())
        self.assertEqual(states, {matched.item_id: 1, corrected.item_id: 0})

    def test_take_ambiguous_can_use_authorized_item_outside_ai_candidates(self):
        candidate = self.add_present(label="candidate")
        corrected = self.add_present(label="manual correction")
        self.vision.status = "AMBIGUOUS"
        self.vision.candidates = [{"item_id": candidate.item_id, "similarity": 0.88}]
        inspection = self.inspect(Action.TAKE_OUT)
        result = self.commit(
            inspection, action="TAKE_OUT", label=None, add_as_new=False,
            selected_item_id=corrected.item_id,
        )
        self.assertEqual(result.decision.item_id, corrected.item_id)

    def test_take_authorized_snapshot_is_rechecked_at_commit(self):
        item = self.add_present(label="removed after inspection")
        self.vision.status = "MATCHED"
        self.vision.candidates = [{"item_id": item.item_id, "similarity": 0.94}]
        inspection = self.inspect(Action.TAKE_OUT)
        with self.repo.connection:
            self.repo.connection.execute(
                "UPDATE inventory SET present=0 WHERE item_id=?", (item.item_id,)
            )
        with self.assertRaisesRegex(InspectionError, "no longer available"):
            self.commit(
                inspection, action="TAKE_OUT", label=None, add_as_new=False,
                selected_item_id=item.item_id,
            )

    def test_action_and_identity_are_bound_to_inspection(self):
        result = self.inspect(Action.PUT_IN)
        with self.assertRaisesRegex(InspectionError, "does not match"):
            self.commit(result, action="TAKE_OUT")
        other_login = self.service._issue_login(self.other)
        with self.assertRaisesRegex(InspectionError, "another signed-in user"):
            self.commit(result, token=other_login.token)

    def test_localization_failure_is_non_committable(self):
        self.vision.localization_status = "UNKNOWN_LOCALIZATION"
        result = self.inspect()
        self.assertFalse(result["committable"])
        with self.assertRaisesRegex(InspectionError, "rescan"):
            self.commit(result)

    def test_gallery_uses_only_dinov2_not_hsv_templates(self):
        item = self.repo.add_item(self.owner.user_id, "old")
        self.repo.add_item_templates(item.item_id, [b"hsv"], "spatial-hsv-f32-v1")
        self.inspect()
        self.assertEqual(self.vision.gallery, [])


if __name__ == "__main__":
    unittest.main()
