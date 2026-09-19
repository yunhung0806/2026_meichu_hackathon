from __future__ import annotations

import json
import math
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from fridge_guardian.adapters.item_vision import ItemVisionClient, ItemVisionClientError
from services.item_vision.category_v2 import (
    CategoryModelSpec,
    TorchScriptCategoryClassifier,
    build_preprocess,
    category_result_from_probabilities,
    sha256_file,
    verify_artifact,
)
from services.item_vision.common import (
    EMBEDDING_DIMENSIONS,
    rank_gallery,
    validate_gallery,
)
from services.item_vision.server import ItemVisionPipeline, resolve_device, validate_dino_features


def vector(x: float = 1.0, y: float = 0.0) -> list[float]:
    result = [0.0] * EMBEDDING_DIMENSIONS
    result[0], result[1] = x, y
    return result


def template(item_id: str, values: list[float]) -> dict:
    return {"item_id": item_id, "crop_embedding": values, "roi_embedding": values}


def response(request_id: str) -> dict:
    match = {
        "status": "NO_MATCH", "candidates": [], "first_item_id": None,
        "first_similarity": 0.0, "second_item_id": None,
        "second_similarity": 0.0, "margin": 0.0,
    }
    return {
        "protocol_version": "item-vision-v1", "request_id": request_id,
        "localization": {"status": "OK", "box": [1, 2, 3, 4]},
        "category": {"status": "UNKNOWN_CATEGORY", "top3": [{"label": "beverage", "score": 0.2}], "margin": 0.01},
        "instance": match, "roi_instance": match,
        "embeddings": {"kind": "dinov2-vits14-crop-f32-v1", "dimensions": 384, "crop": vector(), "roi": vector()},
        "models": {}, "latency_ms": {"total": 1.0},
    }


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return json.dumps(self.payload).encode()


class FakeTransforms:
    calls = []
    class InterpolationMode: BICUBIC = "bicubic"
    @classmethod
    def Compose(cls, steps): cls.calls.append(("Compose", steps)); return steps
    @classmethod
    def Resize(cls, size, interpolation): cls.calls.append(("Resize", size, interpolation)); return ("resize", size)
    @classmethod
    def CenterCrop(cls, size): cls.calls.append(("CenterCrop", size)); return ("crop", size)
    @classmethod
    def ToTensor(cls): return ("tensor",)
    @classmethod
    def Normalize(cls, mean, std): cls.calls.append(("Normalize", tuple(mean), tuple(std))); return ("normalize",)


class FakeModel:
    def eval(self): return self


class FakeTorch:
    def __init__(self):
        self.loaded = None
        self.jit = SimpleNamespace(load=self.load)
    def load(self, path, map_location):
        self.loaded = (path, map_location)
        return FakeModel()
    @staticmethod
    def inference_mode(): return nullcontext()


class ItemVisionProtocolTests(unittest.TestCase):
    def test_gallery_validation_rejects_bad_dimensions_nonfinite_zero_and_size(self):
        with self.assertRaisesRegex(ValueError, "exactly 384"):
            validate_gallery([template("one", [1.0])])
        bad = vector(); bad[5] = math.nan
        with self.assertRaisesRegex(ValueError, "finite"):
            validate_gallery([template("one", bad)])
        with self.assertRaisesRegex(ValueError, "non-zero"):
            validate_gallery([template("one", [0.0] * 384)])
        with self.assertRaisesRegex(ValueError, "100-entry"):
            validate_gallery([template(f"item-{i}", vector()) for i in range(101)])

    def test_gallery_validation_rejects_unknown_fields_and_invalid_ids(self):
        invalid = template("bad id", vector())
        with self.assertRaisesRegex(ValueError, "item_id"):
            validate_gallery([invalid])
        extra = template("valid", vector()); extra["owner_id"] = "secret"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            validate_gallery([extra])

    def test_sidecar_rejects_unknown_request_fields(self):
        pipeline = ItemVisionPipeline.__new__(ItemVisionPipeline)
        pipeline.config = {"instance": {"maximum_gallery_entries": 100}}
        with self.assertRaisesRegex(ValueError, "Unsupported request fields"):
            pipeline._validate_request({
                "roi_jpeg_base64": "unused", "gallery": [], "user_id": "must-not-cross-boundary"
            })

    def test_ranking_returns_ambiguous_in_sidecar_order_and_limits_three(self):
        near = vector(0.999, math.sqrt(1 - 0.999**2))
        gallery = [template("a", vector()), template("b", near), template("c", vector(0.8, 0.6)), template("d", vector(0.7, 0.7))]
        ranked = rank_gallery(vector(), gallery, "crop_embedding", 0.72, 0.04)
        self.assertEqual(ranked["status"], "AMBIGUOUS")
        self.assertEqual([item["item_id"] for item in ranked["candidates"][:2]], ["a", "b"])
        self.assertLessEqual(len(ranked["candidates"]), 3)

    def test_ranking_empty_or_below_threshold_is_no_match(self):
        self.assertEqual(rank_gallery(vector(), [], "crop_embedding", 0.72, 0.04)["status"], "NO_MATCH")
        self.assertEqual(rank_gallery(vector(), [template("x", vector(0, 1))], "crop_embedding", 0.72, 0.04)["candidates"], [])

    def test_unknown_category_keeps_top3_semantics(self):
        result = category_result_from_probabilities([0.21, 0.20, 0.19], ["a", "b", "c"], 0.25, 0.03)
        self.assertEqual(result["status"], "UNKNOWN_CATEGORY")
        self.assertEqual([item["label"] for item in result["top3"]], ["a", "b", "c"])

    def test_checksum_and_metadata_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "model.pt"
            artifact.write_bytes(b"artifact")
            with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
                verify_artifact(artifact, "0" * 64)

    def test_clip_v2_preprocessing_and_torchscript_device_follow_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "model.pt"; artifact.write_bytes(b"verified")
            metadata = root / "metadata.json"
            metadata.write_text(json.dumps({
                "model_version": "clip-v2-test", "artifact_sha256": sha256_file(artifact),
                "labels": ["beverage", "apple", "banana"],
                "preprocessing": {
                    "resize_short_edge": 224, "center_crop": 224,
                    "color_mode": "RGB", "interpolation": "bicubic",
                    "normalize": {"mean": [0.1, 0.2, 0.3], "std": [0.4, 0.5, 0.6]},
                },
            }), encoding="utf-8")
            spec = CategoryModelSpec.from_path(metadata)
            FakeTransforms.calls.clear(); build_preprocess(spec, FakeTransforms)
            self.assertIn(("Resize", 224, "bicubic"), FakeTransforms.calls)
            self.assertIn(("Normalize", (0.1, 0.2, 0.3), (0.4, 0.5, 0.6)), FakeTransforms.calls)
            torch = FakeTorch()
            TorchScriptCategoryClassifier(
                artifact, metadata, "cpu", "CPU",
                {"minimum_top1_probability": 0.25, "minimum_margin": 0.03},
                torch_module=torch, transforms_module=FakeTransforms,
            )
            self.assertEqual(torch.loaded, (str(artifact), "cpu"))

    def test_device_cpu_fallback_and_unavailable_cuda_are_explicit(self):
        fake = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
        self.assertEqual(resolve_device("auto", fake)[0], "cpu")
        with self.assertRaisesRegex(RuntimeError, "torch.cuda is unavailable"):
            resolve_device("cuda:0", fake)

    def test_dino_dimension_is_exactly_384(self):
        validate_dino_features(np.ones((2, 384), dtype=np.float32))
        with self.assertRaisesRegex(RuntimeError, "384"):
            validate_dino_features(np.ones((2, 383), dtype=np.float32))

    def test_client_sends_only_roi_gallery_request_id_and_preserves_unknown_category_embedding(self):
        captured = {}
        def urlopen(request, timeout):
            captured.update(json.loads(request.data))
            return FakeResponse(response("request-1"))
        client = ItemVisionClient("http://127.0.0.1:8765", 1.0)
        with patch("urllib.request.urlopen", side_effect=urlopen):
            result = client.infer(np.zeros((20, 30, 3), dtype=np.uint8), [], request_id="request-1")
        self.assertEqual(set(captured), {"request_id", "roi_jpeg_base64", "gallery"})
        self.assertEqual(result["category"]["status"], "UNKNOWN_CATEGORY")
        self.assertEqual(len(result["embeddings"]["crop"]), 384)

    def test_client_rejects_protocol_mismatch_and_non_loopback(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            ItemVisionClient("http://example.com:8765")
        bad = response("request-1"); bad["protocol_version"] = "item-vision-v0"
        with patch("urllib.request.urlopen", return_value=FakeResponse(bad)):
            with self.assertRaisesRegex(ItemVisionClientError, "Incompatible"):
                ItemVisionClient().infer(np.zeros((10, 10, 3), dtype=np.uint8), [], request_id="request-1")


if __name__ == "__main__":
    unittest.main()
