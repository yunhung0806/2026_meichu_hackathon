from __future__ import annotations

import base64
import re
from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image


ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(value))
    if norm <= 1e-12:
        raise ValueError("Cannot normalize an empty embedding")
    return value / norm


def encode_jpeg_rgb(image: Image.Image, quality: int = 90) -> str:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def decode_jpeg_rgb(value: str) -> Image.Image:
    raw = base64.b64decode(value, validate=True)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("ROI JPEG exceeds the 4 MiB limit")
    with Image.open(BytesIO(raw)) as image:
        return image.convert("RGB")


def select_box(
    candidates: list[dict[str, Any]],
    width: int,
    height: int,
    settings: dict[str, float],
) -> dict[str, Any] | None:
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


def padded_box(
    box: list[int] | tuple[int, int, int, int],
    width: int,
    height: int,
    fraction: float,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    pad_x = int(round((x2 - x1) * fraction))
    pad_y = int(round((y2 - y1) * fraction))
    return max(0, x1 - pad_x), max(0, y1 - pad_y), min(width, x2 + pad_x), min(height, y2 + pad_y)


def rank_gallery(
    query: np.ndarray,
    gallery: list[dict[str, Any]],
    embedding_key: str,
    minimum_similarity: float,
    minimum_margin: float,
) -> dict[str, Any]:
    if not gallery:
        return _unknown_match()
    query = normalize(query)
    scores_by_item: dict[str, list[float]] = {}
    for template in gallery:
        item_id = str(template["item_id"])
        score = float(query @ normalize(np.asarray(template[embedding_key], dtype=np.float32)))
        scores_by_item.setdefault(item_id, []).append(score)
    ranked = sorted(
        ((max(scores), item_id) for item_id, scores in scores_by_item.items()),
        reverse=True,
    )
    first_score, first_item = ranked[0]
    second_score, second_item = ranked[1] if len(ranked) > 1 else (0.0, None)
    margin = first_score - second_score
    status = "MATCHED" if first_score >= minimum_similarity and margin >= minimum_margin else "UNKNOWN_INSTANCE"
    return {
        "status": status,
        "first_item_id": first_item,
        "first_similarity": first_score,
        "second_item_id": second_item,
        "second_similarity": second_score,
        "margin": margin,
    }


def _unknown_match() -> dict[str, Any]:
    return {
        "status": "UNKNOWN_INSTANCE",
        "first_item_id": None,
        "first_similarity": 0.0,
        "second_item_id": None,
        "second_similarity": 0.0,
        "margin": 0.0,
    }
