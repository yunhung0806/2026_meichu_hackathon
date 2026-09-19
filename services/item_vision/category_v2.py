from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class CategoryModelSpec:
    version: str
    labels: tuple[str, ...]
    input_size: int
    resize_short_edge: int
    color_mode: str
    interpolation: str
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    artifact_sha256: str

    @classmethod
    def from_path(cls, path: Path) -> "CategoryModelSpec":
        payload = json.loads(path.read_text(encoding="utf-8"))
        preprocessing = payload["preprocessing"]
        normalization = preprocessing["normalize"]
        labels = tuple(str(label) for label in payload["labels"])
        if not labels or len(set(labels)) != len(labels):
            raise ValueError("Category labels must be non-empty and unique")
        spec = cls(
            version=str(payload["model_version"]),
            labels=labels,
            input_size=int(preprocessing["center_crop"]),
            resize_short_edge=int(preprocessing["resize_short_edge"]),
            color_mode=str(preprocessing["color_mode"]),
            interpolation=str(preprocessing["interpolation"]).lower(),
            mean=tuple(float(value) for value in normalization["mean"]),
            std=tuple(float(value) for value in normalization["std"]),
            artifact_sha256=str(payload["artifact_sha256"]).lower(),
        )
        if spec.color_mode != "RGB" or len(spec.mean) != 3 or len(spec.std) != 3:
            raise ValueError("CLIP v2 preprocessing must use normalized three-channel RGB")
        if spec.input_size <= 0 or spec.resize_short_edge <= 0:
            raise ValueError("CLIP v2 image sizes must be positive")
        return spec


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(path: Path, expected_sha256: str) -> str:
    actual = sha256_file(path)
    if actual.lower() != expected_sha256.lower():
        raise RuntimeError(
            f"Checksum mismatch for {path.name}: expected {expected_sha256}, got {actual}"
        )
    return actual


def build_preprocess(spec: CategoryModelSpec, transforms_module: Any | None = None) -> Any:
    if transforms_module is None:
        from torchvision import transforms as transforms_module

    interpolation = getattr(
        transforms_module.InterpolationMode, spec.interpolation.upper(), None
    )
    if interpolation is None:
        raise ValueError(f"Unsupported interpolation: {spec.interpolation}")
    return transforms_module.Compose(
        [
            transforms_module.Resize(spec.resize_short_edge, interpolation=interpolation),
            transforms_module.CenterCrop(spec.input_size),
            transforms_module.ToTensor(),
            transforms_module.Normalize(mean=spec.mean, std=spec.std),
        ]
    )


def category_result_from_probabilities(
    probabilities: Sequence[float],
    labels: Sequence[str],
    minimum_top1_probability: float,
    minimum_margin: float,
) -> dict[str, Any]:
    if len(probabilities) != len(labels) or not labels:
        raise ValueError("Category probabilities and labels must have the same non-zero length")
    values = [float(value) for value in probabilities]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Category probabilities must be finite")
    order = sorted(range(len(values)), key=values.__getitem__, reverse=True)[:3]
    top3 = [{"label": labels[index], "score": values[index]} for index in order]
    margin = top3[0]["score"] - top3[1]["score"] if len(top3) > 1 else top3[0]["score"]
    status = (
        "OK"
        if top3[0]["score"] >= minimum_top1_probability and margin >= minimum_margin
        else "UNKNOWN_CATEGORY"
    )
    return {"status": status, "top3": top3, "margin": margin}


class TorchScriptCategoryClassifier:
    def __init__(
        self,
        artifact_path: Path,
        metadata_path: Path,
        device: str,
        device_name: str,
        settings: dict[str, Any],
        torch_module: Any | None = None,
        transforms_module: Any | None = None,
    ) -> None:
        if torch_module is None:
            import torch as torch_module

        self.torch = torch_module
        self.spec = CategoryModelSpec.from_path(metadata_path)
        self.artifact_path = artifact_path.resolve()
        self.artifact_sha256 = verify_artifact(artifact_path, self.spec.artifact_sha256)
        self.device = device
        self.device_name = device_name
        self.minimum_top1_probability = float(settings["minimum_top1_probability"])
        self.minimum_margin = float(settings["minimum_margin"])
        self.preprocess = build_preprocess(self.spec, transforms_module)
        try:
            self.model = self.torch.jit.load(str(artifact_path), map_location=device).eval()
        except Exception as exc:
            raise RuntimeError(
                f"CLIP v2 TorchScript could not load on {device}. Use a TorchScript artifact "
                "exported for this device/runtime; do not silently substitute another classifier. "
                f"Original error: {exc}"
            ) from exc

    def classify(self, image: Any) -> dict[str, Any]:
        started = time.perf_counter()
        if getattr(image, "mode", "RGB") != "RGB":
            image = image.convert("RGB")
        tensor = self.preprocess(image).unsqueeze(0).to(self.device)
        with self.torch.inference_mode():
            logits = self.model(tensor)
            probabilities = self.torch.softmax(logits.float(), dim=1)[0]
        result = category_result_from_probabilities(
            probabilities.detach().cpu().tolist(),
            self.spec.labels,
            self.minimum_top1_probability,
            self.minimum_margin,
        )
        result.update(
            latency_ms=(time.perf_counter() - started) * 1000.0,
            device=self.device_name,
            model_version=self.spec.version,
            model_sha256=self.artifact_sha256,
        )
        return result

    def metadata(self) -> dict[str, Any]:
        return {
            "name": "OpenAI CLIP ViT-B/32 item classifier",
            "version": self.spec.version,
            "sha256": self.artifact_sha256,
            "size_bytes": self.artifact_path.stat().st_size,
            "device": self.device_name,
            "labels": list(self.spec.labels),
            "input_size": self.spec.input_size,
            "color_mode": self.spec.color_mode,
            "normalize_mean": list(self.spec.mean),
            "normalize_std": list(self.spec.std),
        }
