from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from fridge_guardian.domain import FaceTemplate, FrameSample, IdentityResult, utc_now


class SFaceIdentityProvider:
    """Local YuNet detection plus SFace embeddings; no image leaves memory."""

    def __init__(
        self,
        detector_path: str | Path,
        recognizer_path: str | Path,
        threshold: float = 0.45,
        ambiguity_margin: float = 0.05,
        detection_threshold: float = 0.70,
    ) -> None:
        import cv2
        import numpy as np

        detector_path = Path(detector_path)
        recognizer_path = Path(recognizer_path)
        if not detector_path.is_file() or not recognizer_path.is_file():
            raise FileNotFoundError(
                "YuNet/SFace models are missing. Run: uv run python scripts/download_models.py"
            )
        self.cv2 = cv2
        self.np = np
        # The OpenCV Windows file-path overload cannot reliably open ONNX files
        # below a Unicode directory. Reading with pathlib and using the buffer
        # overload keeps the repository runnable from paths such as 文件/黑客松.
        detector_buffer = np.frombuffer(detector_path.read_bytes(), dtype=np.uint8)
        recognizer_buffer = np.frombuffer(recognizer_path.read_bytes(), dtype=np.uint8)
        empty_config = np.empty(0, dtype=np.uint8)
        self.detector = cv2.FaceDetectorYN.create(
            "ONNX", detector_buffer, empty_config, (320, 320), detection_threshold, 0.3, 5000
        )
        self.recognizer = cv2.FaceRecognizerSF.create(
            "ONNX", recognizer_buffer, empty_config
        )
        self.threshold = threshold
        self.ambiguity_margin = ambiguity_margin

    def detect_for_preview(self, frame) -> list[tuple[int, int, int, int, float]]:
        """Return boxes for UI guidance without retaining the frame."""
        faces = self._detect(frame)
        if faces is None:
            return []
        return [
            (int(face[0]), int(face[1]), int(face[2]), int(face[3]), float(face[-1]))
            for face in faces
        ]

    def _detect(self, frame):
        height, width = frame.shape[:2]
        self.detector.setInputSize((width, height))
        _, faces = self.detector.detect(frame)
        return faces

    def _features(self, frames: Sequence[FrameSample]) -> list[object]:
        features: list[object] = []
        for sample in frames:
            frame = sample.image
            faces = self._detect(frame)
            if faces is None or len(faces) != 1:
                continue
            aligned = self.recognizer.alignCrop(frame, faces[0])
            feature = self.recognizer.feature(aligned).flatten().astype(self.np.float32)
            norm = float(self.np.linalg.norm(feature))
            if norm > 0:
                features.append(feature / norm)
        return features

    def extract_templates(self, session_id: str, frames: Sequence[FrameSample]) -> list[bytes]:
        self._validate_session(session_id, frames)
        return [feature.tobytes() for feature in self._features(frames)]

    def identify(
        self,
        session_id: str,
        frames: Sequence[FrameSample],
        candidates: Sequence[FaceTemplate],
    ) -> IdentityResult:
        self._validate_session(session_id, frames)
        query_features = self._features(frames)
        if not query_features or not candidates:
            return IdentityResult(session_id, None, 0.0, utc_now())
        grouped: dict[str, list[object]] = defaultdict(list)
        for template in candidates:
            grouped[template.user_id].append(self.np.frombuffer(template.feature, dtype=self.np.float32))
        scores: dict[str, float] = {}
        for user_id, enrolled in grouped.items():
            per_query = [max(float(self.np.dot(query, known)) for known in enrolled) for query in query_features]
            scores[user_id] = float(self.np.median(per_query))
        ranked = sorted(scores.items(), key=lambda entry: entry[1], reverse=True)
        best_id, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else -1.0
        if best_score < self.threshold or best_score - second_score < self.ambiguity_margin:
            return IdentityResult(session_id, None, max(0.0, best_score), utc_now())
        return IdentityResult(session_id, best_id, best_score, utc_now())

    @staticmethod
    def _validate_session(session_id: str, frames: Sequence[FrameSample]) -> None:
        if not frames or any(frame.session_id != session_id for frame in frames):
            raise ValueError("IdentityProvider received frames from different sessions")
