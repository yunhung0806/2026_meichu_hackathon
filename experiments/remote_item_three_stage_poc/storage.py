from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from common import ITEM_ID_PATTERN, normalize


class LocalTemplateStore:
    """Stores item crops and item embeddings only on the Windows client."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def gallery(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for path in sorted(self.root.glob("*/*.npz")):
            with np.load(path, allow_pickle=False) as data:
                records.append(
                    {
                        "item_id": path.parent.name,
                        "crop_embedding": normalize(data["crop_embedding"]).tolist(),
                        "roi_embedding": normalize(data["roi_embedding"]).tolist(),
                    }
                )
        return records

    def enroll(
        self,
        item_id: str,
        crop_bgr: np.ndarray,
        crop_embedding: list[float],
        roi_embedding: list[float],
        metadata: dict[str, object],
    ) -> str:
        if not ITEM_ID_PATTERN.fullmatch(item_id):
            raise ValueError("item_id must use 1-64 ASCII letters, digits, '_' or '-'")
        item_dir = self.root / item_id
        item_dir.mkdir(parents=True, exist_ok=True)
        template_id = uuid4().hex
        crop_path = item_dir / f"{template_id}_crop.jpg"
        temporary_crop = item_dir / f".{template_id}_crop.tmp.jpg"
        encoded, jpeg = cv2.imencode(
            ".jpg", crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 94]
        )
        if not encoded:
            raise RuntimeError("Failed to save item crop")
        temporary_crop.write_bytes(jpeg.tobytes())
        os.replace(temporary_crop, crop_path)
        embedding_path = item_dir / f"{template_id}.npz"
        temporary_embedding = item_dir / f".{template_id}.tmp"
        with temporary_embedding.open("wb") as handle:
            np.savez_compressed(
                handle,
                crop_embedding=normalize(np.asarray(crop_embedding, dtype=np.float32)),
                roi_embedding=normalize(np.asarray(roi_embedding, dtype=np.float32)),
            )
        os.replace(temporary_embedding, embedding_path)
        payload = {
            **metadata,
            "item_id": item_id,
            "template_id": template_id,
            "created_at": datetime.now(UTC).isoformat(),
            "privacy": "Only item crop and item embeddings are saved; no full camera frame",
        }
        (item_dir / f"{template_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return template_id
