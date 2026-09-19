from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import normalize, rank_gallery, select_box  # noqa: E402


class CommonTests(unittest.TestCase):
    def test_gallery_match_and_unknown_margin(self) -> None:
        gallery = [
            {"item_id": "A", "crop_embedding": [1.0, 0.0], "roi_embedding": [1.0, 0.0]},
            {"item_id": "B", "crop_embedding": [0.0, 1.0], "roi_embedding": [0.0, 1.0]},
        ]
        matched = rank_gallery(np.array([1.0, 0.0]), gallery, "crop_embedding", 0.7, 0.1)
        self.assertEqual(matched["status"], "MATCHED")
        self.assertEqual(matched["first_item_id"], "A")
        ambiguous = rank_gallery(np.array([1.0, 1.0]), gallery, "crop_embedding", 0.5, 0.1)
        self.assertEqual(ambiguous["status"], "UNKNOWN_INSTANCE")

    def test_empty_gallery_is_unknown(self) -> None:
        result = rank_gallery(np.array([1.0, 0.0]), [], "crop_embedding", 0.7, 0.1)
        self.assertEqual(result["status"], "UNKNOWN_INSTANCE")
        self.assertIsNone(result["first_item_id"])

    def test_select_box_filters_tiny_and_prefers_central_candidate(self) -> None:
        settings = {
            "minimum_area_fraction": 0.02,
            "maximum_area_fraction": 0.9,
            "centrality_weight": 0.1,
        }
        candidates = [
            {"box": [1, 1, 5, 5], "score": 0.99, "label": "tiny"},
            {"box": [0, 20, 35, 80], "score": 0.70, "label": "edge"},
            {"box": [35, 30, 65, 70], "score": 0.68, "label": "center"},
        ]
        selected = select_box(candidates, 100, 100, settings)
        self.assertEqual(selected["label"], "center")

    def test_zero_embedding_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize(np.zeros(3))


if __name__ == "__main__":
    unittest.main()
