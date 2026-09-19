from __future__ import annotations

import unittest

import cv2
import numpy as np

from fridge_guardian.adapters.item import SpatialHistogramItemRecognizer
from fridge_guardian.domain import FrameSample, ItemTemplate, utc_now


def patterned_frames(session_id: str, bgr: tuple[int, int, int]):
    result = []
    for offset in range(4):
        image = np.zeros((360, 640, 3), dtype=np.uint8)
        x1, y1, x2, y2 = SpatialHistogramItemRecognizer.roi_pixels(image)
        image[y1:y2, x1:x2] = bgr
        for x in range(x1 + offset, x2, 18):
            cv2.line(image, (x, y1), (x, y2), (240, 240, 240), 3)
        result.append(FrameSample(session_id, utc_now(), image))
    return result


class ItemAdapterTests(unittest.TestCase):
    def test_recognizes_enrolled_pattern_and_rejects_different_color(self):
        recognizer = SpatialHistogramItemRecognizer(threshold=0.86)
        enrolled_frames = patterned_frames("enroll", (30, 30, 220))
        templates = [
            ItemTemplate("red-item", feature)
            for feature in recognizer.extract_templates("enroll", enrolled_frames)
        ]
        same = recognizer.identify("same", patterned_frames("same", (30, 30, 220)), templates)
        different = recognizer.identify(
            "different", patterned_frames("different", (220, 30, 30)), templates
        )
        self.assertEqual(same.item_id, "red-item")
        self.assertGreaterEqual(same.confidence, 0.86)
        self.assertIsNone(different.item_id)


if __name__ == "__main__":
    unittest.main()
