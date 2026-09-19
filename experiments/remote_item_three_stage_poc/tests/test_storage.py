from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import LocalTemplateStore  # noqa: E402


class StorageTests(unittest.TestCase):
    def test_saves_crop_and_embeddings_without_full_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalTemplateStore(root)
            crop = np.full((30, 40, 3), 120, dtype=np.uint8)
            store.enroll("bottle_a", crop, [1.0, 0.0], [0.0, 1.0], {"test": True})
            files = [path.name for path in root.rglob("*") if path.is_file()]
            self.assertTrue(any(name.endswith("_crop.jpg") for name in files))
            self.assertTrue(any(name.endswith(".npz") for name in files))
            self.assertFalse(any("frame" in name or "roi.jpg" in name for name in files))
            gallery = store.gallery()
            self.assertEqual(gallery[0]["item_id"], "bottle_a")

    def test_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalTemplateStore(Path(directory))
            with self.assertRaises(ValueError):
                store.enroll("../escape", np.zeros((2, 2, 3), dtype=np.uint8), [1.0], [1.0], {})

    def test_saves_crop_under_unicode_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "測試路徑"
            store = LocalTemplateStore(root)
            crop = np.full((12, 16, 3), 180, dtype=np.uint8)
            store.enroll("carton_a", crop, [1.0, 0.0], [0.0, 1.0], {})
            self.assertEqual(len(list(root.rglob("*_crop.jpg"))), 1)
            self.assertEqual(store.gallery()[0]["item_id"], "carton_a")


if __name__ == "__main__":
    unittest.main()
