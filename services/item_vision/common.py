from __future__ import annotations

import base64
import binascii
import re
from io import BytesIO
from typing import Any

import numpy as np


ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PROTOCOL_VERSION = "item-vision-v1"
EMBEDDING_KIND = "dinov2-vits14-crop-f32-v1"
EMBEDDING_DIMENSIONS = 384
MAX_GALLERY_ENTRIES = 100


def normalize(vector: Any, dimensions: int | None = None) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32).reshape(-1)
    if dimensions is not None and value.size != dimensions:
        raise ValueError(f"Embedding must contain exactly {dimensions} values")
    if value.size == 0 or not np.isfinite(value).all():
        raise ValueError("Embedding must contain finite values")
    norm = float(np.linalg.norm(value))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise ValueError("Embedding must have non-zero norm")
    return (value / norm).astype(np.float32)


def validate_gallery(
    gallery: Any,
    *,
    dimensions: int = EMBEDDING_DIMENSIONS,
    maximum_entries: int = MAX_GALLERY_ENTRIES,
) -> list[dict[str, Any]]:
    if not isinstance(gallery, list):
        raise ValueError("gallery must be a list")
    if len(gallery) > maximum_entries:
        raise ValueError(f"gallery exceeds the {maximum_entries}-entry limit")
    validated: list[dict[str, Any]] = []
    for index, template in enumerate(gallery):
        if not isinstance(template, dict) or set(template) != {
            "item_id", "crop_embedding", "roi_embedding"
        }:
            raise ValueError(f"gallery[{index}] has unsupported or missing fields")
        item_id = template.get("item_id")
        if not isinstance(item_id, str) or not ITEM_ID_PATTERN.fullmatch(item_id):
            raise ValueError(f"gallery[{index}].item_id is invalid")
        validated.append(
            {
                "item_id": item_id,
                "crop_embedding": normalize(template["crop_embedding"], dimensions).tolist(),
                "roi_embedding": normalize(template["roi_embedding"], dimensions).tolist(),
            }
        )
    return validated


def decode_jpeg_rgb(value: str) -> Any:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("roi_jpeg_base64 is not valid base64") from exc
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("ROI JPEG exceeds the 4 MiB limit")
    try:
        from PIL import Image

        with Image.open(BytesIO(raw)) as image:
            if image.format != "JPEG":
                raise ValueError("ROI payload must be a JPEG image")
            return image.convert("RGB")
    except OSError as exc:
        raise ValueError("ROI payload is not a readable JPEG image") from exc


def select_box(candidates: list[dict[str, Any]], width: int, height: int, settings: dict[str, float]) -> dict[str, Any] | None:
    valid: list[tuple[float, dict[str, Any]]] = []
    image_area = float(width * height)
    for candidate in candidates:
        x1, y1, x2, y2 = (float(value) for value in candidate["box"])
        area_fraction = max(0.0, x2 - x1) * max(0.0, y2 - y1) / image_area
        if not settings["minimum_area_fraction"] <= area_fraction <= settings["maximum_area_fraction"]:
            continue
        center_x = (x1 + x2) / (2.0 * width)
        center_y = (y1 + y2) / (2.0 * height)
        distance = min(1.0, ((center_x - 0.5) ** 2 + (center_y - 0.5) ** 2) ** 0.5 / 0.7071)
        combined = float(candidate["score"]) + settings["centrality_weight"] * (1.0 - distance)
        valid.append((combined, candidate))
    return max(valid, key=lambda entry: entry[0])[1] if valid else None


def padded_box(box: list[int] | tuple[int, int, int, int], width: int, height: int, fraction: float) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    pad_x = int(round((x2 - x1) * fraction))
    pad_y = int(round((y2 - y1) * fraction))
    return max(0, x1 - pad_x), max(0, y1 - pad_y), min(width, x2 + pad_x), min(height, y2 + pad_y)


def rank_gallery(query: Any, gallery: list[dict[str, Any]], embedding_key: str, minimum_similarity: float, minimum_margin: float) -> dict[str, Any]:
    if not gallery:
        return _no_match(minimum_similarity, minimum_margin)
    query_vector = normalize(query, EMBEDDING_DIMENSIONS)
    scores_by_item: dict[str, list[float]] = {}
    for template in gallery:
        score = float(query_vector @ normalize(template[embedding_key], EMBEDDING_DIMENSIONS))
        scores_by_item.setdefault(template["item_id"], []).append(score)
    ranked = sorted(
        ((max(scores), item_id) for item_id, scores in scores_by_item.items()),
        key=lambda entry: (-entry[0], entry[1]),
    )
    first_score, first_item = ranked[0]
    second_score, second_item = ranked[1] if len(ranked) > 1 else (0.0, None)
    margin = first_score - second_score
    if first_score < minimum_similarity:
        return _no_match(minimum_similarity, minimum_margin)
    return {
        "status": "MATCHED" if margin >= minimum_margin else "AMBIGUOUS",
        "candidates": [{"item_id": item_id, "similarity": score} for score, item_id in ranked[:3]],
        "first_item_id": first_item,
        "first_similarity": first_score,
        "second_item_id": second_item,
        "second_similarity": second_score,
        "margin": margin,
        "minimum_similarity": minimum_similarity,
        "minimum_margin": minimum_margin,
    }


def _no_match(minimum_similarity: float, minimum_margin: float) -> dict[str, Any]:
    return {
        "status": "NO_MATCH", "candidates": [], "first_item_id": None,
        "first_similarity": 0.0, "second_item_id": None, "second_similarity": 0.0,
        "margin": 0.0, "minimum_similarity": minimum_similarity,
        "minimum_margin": minimum_margin,
    }
