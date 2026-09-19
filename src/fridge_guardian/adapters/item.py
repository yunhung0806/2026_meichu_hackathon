from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from fridge_guardian.domain import FrameSample, ItemResult, ItemTemplate, utc_now


ITEM_ROI = (0.55, 0.28, 0.93, 0.80)


class SpatialHistogramItemRecognizer:
    """Small replaceable baseline for three visually distinct item instances."""

    def __init__(self, threshold: float = 0.70, ambiguity_margin: float = 0.035) -> None:
        import cv2
        import numpy as np

        self.cv2 = cv2
        self.np = np
        self.threshold = threshold
        self.ambiguity_margin = ambiguity_margin

    @staticmethod
    def roi_pixels(frame) -> tuple[int, int, int, int]:
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = ITEM_ROI
        return int(width * x1), int(height * y1), int(width * x2), int(height * y2)

    def _feature(self, frame):
        x1, y1, x2, y2 = self.roi_pixels(frame)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        gray = self.cv2.cvtColor(crop, self.cv2.COLOR_BGR2GRAY)
        if float(gray.std()) < 12.0:
            return None
        hsv = self.cv2.cvtColor(crop, self.cv2.COLOR_BGR2HSV)
        parts = []
        for row in range(3):
            for col in range(3):
                cell = hsv[
                    row * hsv.shape[0] // 3 : (row + 1) * hsv.shape[0] // 3,
                    col * hsv.shape[1] // 3 : (col + 1) * hsv.shape[1] // 3,
                ]
                hist = self.cv2.calcHist([cell], [0, 1], None, [12, 6], [0, 180, 0, 256]).flatten()
                parts.append(hist)
        feature = self.np.concatenate(parts).astype(self.np.float32)
        norm = float(self.np.linalg.norm(feature))
        return feature / norm if norm > 0 else None

    def _features(self, frames: Sequence[FrameSample]) -> list[object]:
        return [feature for frame in frames if (feature := self._feature(frame.image)) is not None]

    def extract_templates(self, session_id: str, frames: Sequence[FrameSample]) -> list[bytes]:
        self._validate_session(session_id, frames)
        return [feature.tobytes() for feature in self._features(frames)]

    def identify(
        self,
        session_id: str,
        frames: Sequence[FrameSample],
        candidates: Sequence[ItemTemplate],
    ) -> ItemResult:
        self._validate_session(session_id, frames)
        query_features = self._features(frames)
        if not query_features or not candidates:
            return ItemResult(session_id, None, 0.0, utc_now())
        grouped: dict[str, list[object]] = defaultdict(list)
        for template in candidates:
            grouped[template.item_id].append(self.np.frombuffer(template.feature, dtype=self.np.float32))
        scores: dict[str, float] = {}
        for item_id, enrolled in grouped.items():
            per_query = [max(float(self.np.dot(query, known)) for known in enrolled) for query in query_features]
            scores[item_id] = float(self.np.median(per_query))
        ranked = sorted(scores.items(), key=lambda entry: entry[1], reverse=True)
        best_id, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else -1.0
        if best_score < self.threshold or best_score - second_score < self.ambiguity_margin:
            return ItemResult(session_id, None, max(0.0, best_score), utc_now())
        return ItemResult(session_id, best_id, best_score, utc_now())

    @staticmethod
    def _validate_session(session_id: str, frames: Sequence[FrameSample]) -> None:
        if not frames or any(frame.session_id != session_id for frame in frames):
            raise ValueError("ItemRecognizer received frames from different sessions")
