from __future__ import annotations

import unittest
from dataclasses import replace

import numpy as np

from fridge_guardian.adapters.face import (
    FaceObservation,
    FaceRecognitionSettings,
    aggregate_identity_scores,
    assess_face_quality,
    embedding_inlier_indices,
    select_diverse_observations,
)
from fridge_guardian.domain import IdentityStatus


def unit(*values: float) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


class FaceQualityTests(unittest.TestCase):
    def setUp(self):
        self.settings = FaceRecognitionSettings()

    def quality(self, **overrides):
        values = {
            "face_count": 1,
            "face_width": 160,
            "face_height": 160,
            "frame_width": 640,
            "frame_height": 480,
            "sharpness": 120.0,
            "detection_confidence": 0.92,
            "settings": self.settings,
        }
        values.update(overrides)
        return assess_face_quality(**values)

    def test_accepts_clear_single_face(self):
        self.assertTrue(self.quality().accepted)

    def test_rejects_no_face_multiple_small_and_blurred(self):
        self.assertEqual(self.quality(face_count=0).reason, "no_face")
        self.assertEqual(self.quality(face_count=2).reason, "multiple_faces")
        self.assertEqual(self.quality(face_width=40, face_height=40).reason, "face_too_small")
        self.assertEqual(self.quality(sharpness=10.0).reason, "blurred")


class FaceTemplateSelectionTests(unittest.TestCase):
    def test_embedding_outlier_is_removed(self):
        features = [
            unit(1.0, 0.0, 0.0),
            unit(0.99, 0.08, 0.0),
            unit(0.99, -0.08, 0.0),
            unit(0.0, 1.0, 0.0),
        ]
        self.assertEqual(embedding_inlier_indices(features, 0.50), [0, 1, 2])

    def test_selects_bounded_pose_diverse_templates(self):
        settings = replace(
            FaceRecognitionSettings(),
            target_enrollment_templates=5,
            minimum_enrollment_templates=3,
        )
        observations = []
        poses = ["front", "left", "right", "front", "left", "right"]
        for index, pose in enumerate(poses):
            values = np.zeros(8, dtype=np.float32)
            values[0] = 1.0
            values[index + 1] = 0.18
            observations.append(FaceObservation(unit(*values), pose, 1.0 - index * 0.02))
        selected, outliers, duplicates = select_diverse_observations(observations, settings)
        self.assertEqual(len(selected), 5)
        self.assertEqual(outliers, 0)
        self.assertEqual(duplicates, 1)
        self.assertEqual({item.pose for item in selected}, {"front", "left", "right"})


class FaceAggregationTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(
            FaceRecognitionSettings(),
            minimum_valid_frames=3,
            absolute_threshold=0.75,
            margin_threshold=0.15,
            minimum_vote_ratio=0.66,
            template_top_k=2,
        )

    def test_matches_using_multiple_templates_and_frames(self):
        candidates = {
            "A": [unit(1.0, 0.0), unit(0.99, 0.08)],
            "B": [unit(0.0, 1.0), unit(0.08, 0.99)],
        }
        queries = [unit(1.0, 0.02), unit(0.99, -0.03), unit(1.0, 0.05)]
        result = aggregate_identity_scores("matched", queries, candidates, self.settings)
        self.assertEqual(result.status, IdentityStatus.MATCHED)
        self.assertEqual(result.user_id, "A")
        self.assertEqual(result.valid_frames, 3)
        self.assertGreater(result.margin, self.settings.margin_threshold)

    def test_unknown_when_absolute_score_is_too_low(self):
        candidates = {
            "A": [unit(1.0, 0.0)],
            "B": [unit(0.0, 1.0)],
        }
        queries = [unit(1.0, 1.0)] * 3
        result = aggregate_identity_scores("unknown", queries, candidates, self.settings)
        self.assertEqual(result.status, IdentityStatus.UNKNOWN_USER)
        self.assertIsNone(result.user_id)

    def test_ambiguous_when_best_and_second_are_too_close(self):
        candidates = {
            "A": [unit(1.0, 0.0)],
            "B": [unit(0.98, 0.20)],
        }
        queries = [unit(1.0, 0.01)] * 3
        result = aggregate_identity_scores("ambiguous", queries, candidates, self.settings)
        self.assertEqual(result.status, IdentityStatus.AMBIGUOUS_USER)
        self.assertIsNone(result.user_id)
        self.assertLess(result.margin, self.settings.margin_threshold)

    def test_no_face_when_too_few_valid_frames_remain(self):
        result = aggregate_identity_scores(
            "no-face",
            [unit(1.0, 0.0), unit(1.0, 0.01)],
            {"A": [unit(1.0, 0.0)]},
            self.settings,
        )
        self.assertEqual(result.status, IdentityStatus.NO_FACE)
        self.assertEqual(result.valid_frames, 2)


if __name__ == "__main__":
    unittest.main()
