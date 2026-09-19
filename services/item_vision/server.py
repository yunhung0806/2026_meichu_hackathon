from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import platform
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np

try:
    from .category_v2 import TorchScriptCategoryClassifier
    from .common import (
        EMBEDDING_DIMENSIONS, EMBEDDING_KIND, MAX_GALLERY_ENTRIES,
        PROTOCOL_VERSION, decode_jpeg_rgb, normalize, padded_box,
        rank_gallery, select_box, validate_gallery,
    )
except ImportError:  # Direct `python server.py` execution on Manta.
    from category_v2 import TorchScriptCategoryClassifier
    from common import (
        EMBEDDING_DIMENSIONS, EMBEDDING_KIND, MAX_GALLERY_ENTRIES,
        PROTOCOL_VERSION, decode_jpeg_rgb, normalize, padded_box,
        rank_gallery, select_box, validate_gallery,
    )


ROOT = Path(__file__).resolve().parent
GROUNDING_MODEL = "IDEA-Research/grounding-dino-tiny"
GROUNDING_REVISION = "a2bb814dd30d776dcf7e30523b00659f4f141c71"
GROUNDING_SHA256 = "1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3"
DINO_MODEL = "facebook/dinov2-small"
DINO_REVISION = "ed25f3a31f01632728cabb09d1542f84ab7b0056"
DINO_SHA256 = "ae1e99fcefd534ed978cdeb8326f08030c96e28b7a81ffcbc98a857c84d14be1"
ALLOWED_REQUEST_KEYS = frozenset({"request_id", "roi_jpeg_base64", "gallery"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, expected: str) -> None:
    actual = sha256_file(path)
    if actual.lower() != expected.lower():
        raise RuntimeError(f"Checksum mismatch for {path.name}: {actual}")


def resolve_device(requested: str, torch_module: Any) -> tuple[str, str]:
    value = requested.strip().lower()
    if value == "auto":
        value = "cuda:0" if torch_module.cuda.is_available() else "cpu"
    if value == "cpu":
        return "cpu", platform.processor() or "CPU"
    if value.startswith("cuda"):
        if not torch_module.cuda.is_available():
            raise RuntimeError(
                f"Requested device {requested!r}, but torch.cuda is unavailable. "
                "Use --device cpu for the compatibility smoke test."
            )
        index = int(value.split(":", 1)[1]) if ":" in value else 0
        if index < 0 or index >= torch_module.cuda.device_count():
            raise RuntimeError(f"Requested CUDA device index {index} is unavailable")
        return f"cuda:{index}", torch_module.cuda.get_device_name(index)
    raise ValueError("--device must be auto, cpu, cuda, or cuda:<index>")


class ItemVisionPipeline:
    def __init__(
        self,
        config_path: Path,
        cache_dir: Path,
        category_model_path: Path,
        category_metadata_path: Path,
        requested_device: str = "auto",
    ) -> None:
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoImageProcessor, AutoModel, AutoModelForZeroShotObjectDetection, AutoProcessor

        self.torch = torch
        self.device, self.device_name = resolve_device(requested_device, torch)
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.localization_labels = tuple(self.config["localization_labels"])
        self.prompt = ". ".join(self.localization_labels) + "."
        cache_dir.mkdir(parents=True, exist_ok=True)

        grounding_snapshot = Path(snapshot_download(
            repo_id=GROUNDING_MODEL, revision=GROUNDING_REVISION,
            cache_dir=cache_dir / "huggingface",
            allow_patterns=["*.json", "*.txt", "model.safetensors"],
        ))
        verify(grounding_snapshot / "model.safetensors", GROUNDING_SHA256)
        self.grounding_processor = AutoProcessor.from_pretrained(grounding_snapshot, local_files_only=True)
        self.grounding_model = AutoModelForZeroShotObjectDetection.from_pretrained(
            grounding_snapshot, local_files_only=True, use_safetensors=True, dtype=torch.float32
        ).to(self.device).eval()

        self.category_classifier = TorchScriptCategoryClassifier(
            category_model_path, category_metadata_path, self.device,
            self.device_name, self.config["category"]
        )

        dino_snapshot = Path(snapshot_download(
            repo_id=DINO_MODEL, revision=DINO_REVISION,
            cache_dir=cache_dir / "huggingface",
            allow_patterns=["*.json", "model.safetensors"],
        ))
        verify(dino_snapshot / "model.safetensors", DINO_SHA256)
        self.dino_processor = AutoImageProcessor.from_pretrained(dino_snapshot, local_files_only=True)
        self.dino_model = AutoModel.from_pretrained(
            dino_snapshot, local_files_only=True, use_safetensors=True, dtype=torch.float32
        ).to(self.device).eval()

    def infer(self, request: dict[str, Any]) -> dict[str, Any]:
        total_started = time.perf_counter()
        request_id, roi, gallery = self._validate_request(request)
        localization = self._localize(roi)
        if localization["status"] != "OK":
            total = elapsed_ms(total_started)
            result = {
                "protocol_version": PROTOCOL_VERSION,
                "localization": {**localization, "crop_box": None},
                "category": self._not_run_category(),
                "instance": self._not_run_match(),
                "roi_instance": self._not_run_match(),
                "embeddings": None,
                "models": self._compact_model_identity(),
                "latency_ms": {"localization": localization["latency_ms"], "category": 0.0, "embedding": 0.0, "total": total},
                "total_latency_ms": total,
            }
        else:
            crop_box = padded_box(
                localization["box"], roi.width, roi.height,
                float(self.config["localization"]["padding_fraction"]),
            )
            crop = roi.crop(crop_box)
            category = self.category_classifier.classify(crop)
            crop_embedding, roi_embedding, embedding_latency = self._embed(crop, roi)
            settings = self.config["instance"]
            instance = rank_gallery(crop_embedding, gallery, "crop_embedding", float(settings["minimum_similarity"]), float(settings["minimum_margin"]))
            roi_instance = rank_gallery(roi_embedding, gallery, "roi_embedding", float(settings["minimum_similarity"]), float(settings["minimum_margin"]))
            instance.update(latency_ms=embedding_latency, device=self.device_name)
            roi_instance.update(latency_ms=embedding_latency, device=self.device_name)
            total = elapsed_ms(total_started)
            result = {
                "protocol_version": PROTOCOL_VERSION,
                "localization": {**localization, "crop_box": list(crop_box)},
                "category": category,
                "instance": instance,
                "roi_instance": roi_instance,
                "embeddings": {"kind": EMBEDDING_KIND, "dimensions": EMBEDDING_DIMENSIONS, "crop": crop_embedding.tolist(), "roi": roi_embedding.tolist()},
                "models": self._compact_model_identity(),
                "latency_ms": {"localization": localization["latency_ms"], "category": category["latency_ms"], "embedding": embedding_latency, "total": total},
                "total_latency_ms": total,
            }
        if request_id is not None:
            result["request_id"] = request_id
        return result

    def _validate_request(self, request: dict[str, Any]) -> tuple[str | None, Any, list[dict[str, Any]]]:
        if not isinstance(request, dict):
            raise ValueError("Request JSON must be an object")
        unknown = set(request) - ALLOWED_REQUEST_KEYS
        if unknown:
            raise ValueError(f"Unsupported request fields: {', '.join(sorted(unknown))}")
        request_id = request.get("request_id")
        if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 128):
            raise ValueError("request_id must be a string of at most 128 characters")
        encoded = request.get("roi_jpeg_base64")
        if not isinstance(encoded, str):
            raise ValueError("roi_jpeg_base64 must be a base64 JPEG string")
        return request_id, decode_jpeg_rgb(encoded), validate_gallery(
            request.get("gallery", []), maximum_entries=int(self.config["instance"].get("maximum_gallery_entries", MAX_GALLERY_ENTRIES))
        )

    def _localize(self, image: Any) -> dict[str, Any]:
        started = time.perf_counter()
        inputs = self.grounding_processor(images=image, text=self.prompt, return_tensors="pt")
        model_dtype = next(self.grounding_model.parameters()).dtype
        inputs = {name: value.to(self.device, dtype=model_dtype if value.is_floating_point() else value.dtype) for name, value in inputs.items()}
        with self.torch.inference_mode():
            outputs = self.grounding_model(**inputs)
        postprocess = self.grounding_processor.post_process_grounded_object_detection
        kwargs: dict[str, Any] = {
            "outputs": outputs, "input_ids": inputs.get("input_ids"),
            "text_threshold": float(self.config["localization"]["text_threshold"]),
            "target_sizes": [(image.height, image.width)],
        }
        threshold_name = "box_threshold" if "box_threshold" in inspect.signature(postprocess).parameters else "threshold"
        kwargs[threshold_name] = float(self.config["localization"]["box_threshold"])
        raw = postprocess(**kwargs)[0]
        labels = raw.get("text_labels", raw.get("labels", []))
        candidates = []
        for box, score, label in zip(raw["boxes"], raw["scores"], labels, strict=False):
            values = box.detach().float().cpu().tolist()
            candidates.append({
                "box": [max(0, min(image.width, int(round(values[0])))), max(0, min(image.height, int(round(values[1])))), max(0, min(image.width, int(round(values[2])))), max(0, min(image.height, int(round(values[3]))))],
                "score": float(score.detach().float().cpu()), "label": str(label),
            })
        selected = select_box(candidates, image.width, image.height, self.config["localization"])
        if selected is None:
            return {"status": "UNKNOWN_LOCALIZATION", "score": 0.0, "box": None, "label": None, "latency_ms": elapsed_ms(started), "device": self.device_name}
        return {"status": "OK", **selected, "latency_ms": elapsed_ms(started), "device": self.device_name}

    def _embed(self, crop: Any, roi: Any) -> tuple[np.ndarray, np.ndarray, float]:
        started = time.perf_counter()
        inputs = self.dino_processor(images=[crop, roi], return_tensors="pt")
        model_dtype = next(self.dino_model.parameters()).dtype
        inputs = {name: value.to(self.device, dtype=model_dtype if value.is_floating_point() else value.dtype) for name, value in inputs.items()}
        with self.torch.inference_mode():
            output = self.dino_model(**inputs)
            features = output.last_hidden_state[:, 0, :].detach().float().cpu().numpy()
        validate_dino_features(features)
        return normalize(features[0]), normalize(features[1]), elapsed_ms(started)

    def _compact_model_identity(self) -> dict[str, str]:
        return {"category_version": self.category_classifier.spec.version, "category_sha256": self.category_classifier.artifact_sha256, "instance_kind": EMBEDDING_KIND}

    def _not_run_category(self) -> dict[str, Any]:
        return {"status": "NOT_RUN", "top3": [], "margin": 0.0, "latency_ms": 0.0, "device": self.device_name, "model_version": self.category_classifier.spec.version, "model_sha256": self.category_classifier.artifact_sha256}

    def _not_run_match(self) -> dict[str, Any]:
        settings = self.config["instance"]
        return {"status": "NOT_RUN", "candidates": [], "first_item_id": None, "first_similarity": 0.0, "second_item_id": None, "second_similarity": 0.0, "margin": 0.0, "minimum_similarity": float(settings["minimum_similarity"]), "minimum_margin": float(settings["minimum_margin"])}

    def metadata(self) -> dict[str, Any]:
        grounding_path = Path(self.grounding_model.name_or_path) / "model.safetensors"
        dino_path = Path(self.dino_model.name_or_path) / "model.safetensors"
        return {
            "status": "ready", "protocol_version": PROTOCOL_VERSION,
            "device": self.device_name, "requested_runtime_device": self.device,
            "models": {
                "localization": {"name": GROUNDING_MODEL, "revision": GROUNDING_REVISION, "sha256": GROUNDING_SHA256, "size_bytes": grounding_path.stat().st_size},
                "category": self.category_classifier.metadata(),
                "instance": {"name": DINO_MODEL, "revision": DINO_REVISION, "sha256": DINO_SHA256, "size_bytes": dino_path.stat().st_size, "embedding_kind": EMBEDDING_KIND, "embedding_dimensions": EMBEDDING_DIMENSIONS},
            },
            "localization_labels": list(self.localization_labels),
            "label_taxonomy": list(self.category_classifier.spec.labels),
        }


def validate_dino_features(features: np.ndarray) -> None:
    if features.shape != (2, EMBEDDING_DIMENSIONS):
        raise RuntimeError(f"DINOv2 must return two {EMBEDDING_DIMENSIONS}-dimensional embeddings, got {features.shape}")
    if not np.isfinite(features).all():
        raise RuntimeError("DINOv2 returned non-finite embeddings")


def elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


class Handler(BaseHTTPRequestHandler):
    pipeline: ItemVisionPipeline

    def do_GET(self) -> None:  # noqa: N802
        self._json(200, self.pipeline.metadata()) if self.path == "/health" else self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/infer":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 8 * 1024 * 1024:
                raise ValueError("Request body must be between 1 byte and 8 MiB")
            self._json(200, self.pipeline.infer(json.loads(self.rfile.read(length))))
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.client_address[0]} {format % args}", flush=True)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Loopback item-vision-v1 sidecar")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda, or cuda:<index>")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".cache")
    parser.add_argument("--category-model", type=Path, required=True)
    parser.add_argument("--category-metadata", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        Handler.pipeline = ItemVisionPipeline(
            args.config, args.cache_dir, args.category_model,
            args.category_metadata, args.device,
        )
    except Exception as exc:
        raise SystemExit(f"Item Vision startup failed: {exc}") from exc
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Item Vision ready on http://{args.host}:{args.port} ({Handler.pipeline.device_name})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
