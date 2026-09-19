from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np

from common import decode_jpeg_rgb, normalize, padded_box, rank_gallery, select_box


ROOT = Path(__file__).resolve().parent
GROUNDING_MODEL = "IDEA-Research/grounding-dino-tiny"
GROUNDING_REVISION = "a2bb814dd30d776dcf7e30523b00659f4f141c71"
GROUNDING_SHA256 = "1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3"
DINO_MODEL = "facebook/dinov2-small"
DINO_REVISION = "ed25f3a31f01632728cabb09d1542f84ab7b0056"
DINO_SHA256 = "ae1e99fcefd534ed978cdeb8326f08030c96e28b7a81ffcbc98a857c84d14be1"
CLIP_SHA256 = "40d365715913c9da98579312b702a82c18be219cc2a73407c4526f58eba950af"


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


class RemoteItemPipeline:
    def __init__(self, config_path: Path, cache_dir: Path) -> None:
        import clip
        import torch
        from huggingface_hub import snapshot_download
        from transformers import (
            AutoImageProcessor,
            AutoModel,
            AutoModelForZeroShotObjectDetection,
            AutoProcessor,
        )

        if not torch.cuda.is_available():
            raise RuntimeError("MLSteam ROCm GPU is not available through torch.cuda")
        self.torch = torch
        self.clip = clip
        self.device = "cuda:0"
        self.device_name = torch.cuda.get_device_name(0)
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.labels = tuple(self.config["labels"])
        self.prompt = ". ".join(self.labels) + "."
        cache_dir.mkdir(parents=True, exist_ok=True)

        print("Preparing Grounding DINO-T...", flush=True)
        grounding_snapshot = Path(
            snapshot_download(
                repo_id=GROUNDING_MODEL,
                revision=GROUNDING_REVISION,
                cache_dir=cache_dir / "huggingface",
                allow_patterns=["*.json", "*.txt", "model.safetensors"],
            )
        )
        verify(grounding_snapshot / "model.safetensors", GROUNDING_SHA256)
        self.grounding_processor = AutoProcessor.from_pretrained(
            grounding_snapshot, local_files_only=True
        )
        self.grounding_model = AutoModelForZeroShotObjectDetection.from_pretrained(
            grounding_snapshot,
            local_files_only=True,
            use_safetensors=True,
            dtype=torch.float32,
        ).to(self.device).eval()

        print("Preparing official OpenAI CLIP ViT-B/32...", flush=True)
        clip_root = cache_dir / "openai-clip"
        clip_root.mkdir(parents=True, exist_ok=True)
        self.clip_model, self.clip_preprocess = clip.load(
            "ViT-B/32", device=self.device, download_root=str(clip_root), jit=False
        )
        verify(clip_root / "ViT-B-32.pt", CLIP_SHA256)
        tokens = clip.tokenize([f"a photo of a {label}" for label in self.labels]).to(self.device)
        with torch.inference_mode():
            text_features = self.clip_model.encode_text(tokens)
            self.text_features = text_features / text_features.norm(dim=-1, keepdim=True)

        print("Preparing DINOv2 ViT-S/14...", flush=True)
        dino_snapshot = Path(
            snapshot_download(
                repo_id=DINO_MODEL,
                revision=DINO_REVISION,
                cache_dir=cache_dir / "huggingface",
                allow_patterns=["*.json", "model.safetensors"],
            )
        )
        verify(dino_snapshot / "model.safetensors", DINO_SHA256)
        self.dino_processor = AutoImageProcessor.from_pretrained(
            dino_snapshot, local_files_only=True
        )
        self.dino_model = AutoModel.from_pretrained(
            dino_snapshot,
            local_files_only=True,
            use_safetensors=True,
            dtype=torch.float32,
        ).to(self.device).eval()
        print(f"Models ready on {self.device_name}", flush=True)

    def infer(self, request: dict[str, Any]) -> dict[str, Any]:
        total_started = time.perf_counter()
        roi = decode_jpeg_rgb(str(request["roi_jpeg_base64"]))
        gallery = request.get("gallery", [])
        if not isinstance(gallery, list):
            raise ValueError("gallery must be a list")
        localization = self._localize(roi)
        if localization["status"] != "OK":
            return {
                "localization": localization,
                "category": {"status": "NOT_RUN", "top3": [], "margin": 0.0},
                "instance": _not_run_match(),
                "roi_instance": _not_run_match(),
                "embeddings": None,
                "total_latency_ms": elapsed_ms(total_started),
            }

        crop_box = padded_box(
            localization["box"],
            roi.width,
            roi.height,
            float(self.config["localization"]["padding_fraction"]),
        )
        crop = roi.crop(crop_box)
        category = self._classify(crop)
        crop_embedding, roi_embedding, embedding_latency = self._embed(crop, roi)
        instance_config = self.config["instance"]
        crop_match = rank_gallery(
            crop_embedding,
            gallery,
            "crop_embedding",
            float(instance_config["minimum_similarity"]),
            float(instance_config["minimum_margin"]),
        )
        roi_match = rank_gallery(
            roi_embedding,
            gallery,
            "roi_embedding",
            float(instance_config["minimum_similarity"]),
            float(instance_config["minimum_margin"]),
        )
        crop_match.update({"latency_ms": embedding_latency, "device": self.device_name})
        roi_match.update({"latency_ms": embedding_latency, "device": self.device_name})
        return {
            "localization": {**localization, "crop_box": list(crop_box)},
            "category": category,
            "instance": crop_match,
            "roi_instance": roi_match,
            "embeddings": {
                "crop": crop_embedding.tolist(),
                "roi": roi_embedding.tolist(),
            },
            "total_latency_ms": elapsed_ms(total_started),
        }

    def _localize(self, image: Any) -> dict[str, Any]:
        started = time.perf_counter()
        inputs = self.grounding_processor(images=image, text=self.prompt, return_tensors="pt")
        model_dtype = next(self.grounding_model.parameters()).dtype
        inputs = {
            name: value.to(
                self.device,
                dtype=model_dtype if value.is_floating_point() else value.dtype,
            )
            for name, value in inputs.items()
        }
        with self.torch.inference_mode():
            outputs = self.grounding_model(**inputs)
        postprocess = self.grounding_processor.post_process_grounded_object_detection
        kwargs: dict[str, Any] = {
            "outputs": outputs,
            "input_ids": inputs.get("input_ids"),
            "text_threshold": float(self.config["localization"]["text_threshold"]),
            "target_sizes": [(image.height, image.width)],
        }
        if "box_threshold" in inspect.signature(postprocess).parameters:
            kwargs["box_threshold"] = float(self.config["localization"]["box_threshold"])
        else:
            kwargs["threshold"] = float(self.config["localization"]["box_threshold"])
        raw = postprocess(**kwargs)[0]
        labels = raw.get("text_labels", raw.get("labels", []))
        candidates = []
        for box, score, label in zip(raw["boxes"], raw["scores"], labels, strict=False):
            values = box.detach().float().cpu().tolist()
            candidates.append(
                {
                    "box": [
                        max(0, min(image.width, int(round(values[0])))),
                        max(0, min(image.height, int(round(values[1])))),
                        max(0, min(image.width, int(round(values[2])))),
                        max(0, min(image.height, int(round(values[3])))),
                    ],
                    "score": float(score.detach().float().cpu()),
                    "label": str(label),
                }
            )
        selected = select_box(
            candidates, image.width, image.height, self.config["localization"]
        )
        if selected is None:
            return {
                "status": "UNKNOWN_LOCALIZATION",
                "score": 0.0,
                "box": None,
                "label": None,
                "latency_ms": elapsed_ms(started),
                "device": self.device_name,
            }
        return {
            "status": "OK",
            **selected,
            "latency_ms": elapsed_ms(started),
            "device": self.device_name,
        }

    def _classify(self, image: Any) -> dict[str, Any]:
        started = time.perf_counter()
        tensor = self.clip_preprocess(image).unsqueeze(0).to(self.device)
        with self.torch.inference_mode():
            features = self.clip_model.encode_image(tensor)
            features = features / features.norm(dim=-1, keepdim=True)
            probabilities = (100.0 * features @ self.text_features.T).softmax(dim=-1)[0]
        values = probabilities.detach().float().cpu().numpy()
        order = np.argsort(values)[::-1][:3]
        top3 = [
            {"label": self.labels[int(index)], "score": float(values[int(index)])}
            for index in order
        ]
        margin = top3[0]["score"] - top3[1]["score"] if len(top3) > 1 else top3[0]["score"]
        settings = self.config["category"]
        status = (
            "OK"
            if top3
            and top3[0]["score"] >= float(settings["minimum_top1_probability"])
            and margin >= float(settings["minimum_margin"])
            else "UNKNOWN_CATEGORY"
        )
        return {
            "status": status,
            "top3": top3,
            "margin": margin,
            "latency_ms": elapsed_ms(started),
            "device": self.device_name,
        }

    def _embed(self, crop: Any, roi: Any) -> tuple[np.ndarray, np.ndarray, float]:
        started = time.perf_counter()
        inputs = self.dino_processor(images=[crop, roi], return_tensors="pt")
        model_dtype = next(self.dino_model.parameters()).dtype
        inputs = {
            name: value.to(
                self.device,
                dtype=model_dtype if value.is_floating_point() else value.dtype,
            )
            for name, value in inputs.items()
        }
        with self.torch.inference_mode():
            output = self.dino_model(**inputs)
            features = output.last_hidden_state[:, 0, :].detach().float().cpu().numpy()
        return normalize(features[0]), normalize(features[1]), elapsed_ms(started)


def elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _not_run_match() -> dict[str, Any]:
    return {
        "status": "NOT_RUN",
        "first_item_id": None,
        "first_similarity": 0.0,
        "second_item_id": None,
        "second_similarity": 0.0,
        "margin": 0.0,
    }


class Handler(BaseHTTPRequestHandler):
    pipeline: RemoteItemPipeline

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._json(404, {"error": "not found"})
            return
        self._json(200, {"status": "ready", "device": self.pipeline.device_name})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/infer":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 8 * 1024 * 1024:
                raise ValueError("Request body must be between 1 byte and 8 MiB")
            request = json.loads(self.rfile.read(length))
            response = self.pipeline.infer(request)
            self._json(200, response)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # inference failures stay visible to the local operator
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
    parser = argparse.ArgumentParser(description="Isolated MLSteam item inference server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".cache")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    Handler.pipeline = RemoteItemPipeline(args.config, args.cache_dir)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Listening on http://{args.host}:{args.port}; SSH tunnel required", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
