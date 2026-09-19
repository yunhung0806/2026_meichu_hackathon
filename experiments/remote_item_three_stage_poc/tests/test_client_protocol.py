from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import crop_from_result, remote_infer  # noqa: E402


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return b'{"localization":{"status":"UNKNOWN_LOCALIZATION"}}'


class ClientProtocolTests(unittest.TestCase):
    def test_request_contains_roi_and_gallery_but_no_full_frame(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout):
            captured.update(json.loads(request.data))
            return FakeResponse()

        roi = np.full((20, 30, 3), 80, dtype=np.uint8)
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result, latency = remote_infer("http://127.0.0.1:8765", roi, [], 1.0)
        self.assertIn("roi_jpeg_base64", captured)
        self.assertIn("gallery", captured)
        self.assertNotIn("frame", captured)
        self.assertEqual(result["localization"]["status"], "UNKNOWN_LOCALIZATION")
        self.assertGreaterEqual(latency, 0.0)

    def test_crop_uses_remote_padded_box(self) -> None:
        roi = np.zeros((100, 120, 3), dtype=np.uint8)
        result = {"localization": {"status": "OK", "crop_box": [10, 20, 70, 80]}}
        self.assertEqual(crop_from_result(roi, result).shape, (60, 60, 3))


if __name__ == "__main__":
    unittest.main()
