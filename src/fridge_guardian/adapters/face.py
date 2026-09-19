from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import numpy as np

from fridge_guardian.domain import (
    FaceEnrollmentResult,
    FaceTemplate,
    FrameSample,
    IdentityResult,
    IdentityStatus,
    utc_now,
)


@dataclass(frozen=True)
class FaceRecognitionSettings:
    absolute_threshold: float = 0.55
    margin_threshold: float = 0.12
    minimum_valid_frames: int = 5
    minimum_vote_ratio: float = 0.60
    recognition_window_seconds: float = 1.6
    recognition_candidate_frames: int = 16
    enrollment_window_seconds: float = 3.6
    enrollment_candidate_frames: int = 24
    prepare_seconds: float = 1.0
    minimum_enrollment_templates: int = 5
    target_enrollment_templates: int = 8
    detector_threshold: float = 0.70
    minimum_face_size_px: int = 80
    minimum_face_fraction: float = 0.12
    blur_variance_threshold: float = 50.0
    outlier_median_similarity: float = 0.50
    duplicate_similarity: float = 0.998
    template_top_k: int = 3

    @classmethod
    def load(cls, path: str | Path) -> "FaceRecognitionSettings":
        path = Path(path)
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot load face settings from {path}: {exc}") from exc
        if not isinstance(values, dict):
            raise ValueError("Face settings must be a JSON object")
        allowed = {item.name for item in fields(cls)}
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"Unknown face setting(s): {', '.join(unknown)}")
        settings = cls(**values)
        settings.validate()
        return settings

    def validate(self) -> None:
        probabilities = {
            "absolute_threshold": self.absolute_threshold,
            "margin_threshold": self.margin_threshold,
            "minimum_vote_ratio": self.minimum_vote_ratio,
            "detector_threshold": self.detector_threshold,
            "minimum_face_fraction": self.minimum_face_fraction,
            "outlier_median_similarity": self.outlier_median_similarity,
            "duplicate_similarity": self.duplicate_similarity,
        }
        for name, value in probabilities.items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        positive = {
            "minimum_valid_frames": self.minimum_valid_frames,
            "recognition_window_seconds": self.recognition_window_seconds,
            "recognition_candidate_frames": self.recognition_candidate_frames,
            "enrollment_window_seconds": self.enrollment_window_seconds,
            "enrollment_candidate_frames": self.enrollment_candidate_frames,
            "minimum_enrollment_templates": self.minimum_enrollment_templates,
            "target_enrollment_templates": self.target_enrollment_templates,
            "minimum_face_size_px": self.minimum_face_size_px,
            "blur_variance_threshold": self.blur_variance_threshold,
            "template_top_k": self.template_top_k,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.minimum_enrollment_templates > self.target_enrollment_templates:
            raise ValueError("minimum_enrollment_templates cannot exceed target_enrollment_templates")
        if not 15 <= self.enrollment_candidate_frames <= 30:
            raise ValueError("enrollment_candidate_frames must stay between 15 and 30")


@dataclass(frozen=True)
class FaceQualityResult:
    accepted: bool
    reason: str
    face_fraction: float
    sharpness: float
    score: float


@dataclass(frozen=True)
class FaceObservation:
    feature: np.ndarray
    pose: str
    quality: float


def assess_face_quality(
    *,
    face_count: int,
    face_width: int,
    face_height: int,
    frame_width: int,
    frame_height: int,
    sharpness: float,
    detection_confidence: float,
    settings: FaceRecognitionSettings,
) -> FaceQualityResult:
    """Apply non-biometric capture quality gates without retaining an image."""
    fraction = min(face_width, face_height) / max(1, min(frame_width, frame_height))
    if face_count == 0:
        return FaceQualityResult(False, "no_face", fraction, sharpness, 0.0)
    if face_count != 1:
        return FaceQualityResult(False, "multiple_faces", fraction, sharpness, 0.0)
    if min(face_width, face_height) < settings.minimum_face_size_px:
        return FaceQualityResult(False, "face_too_small", fraction, sharpness, 0.0)
    if fraction < settings.minimum_face_fraction:
        return FaceQualityResult(False, "face_too_small", fraction, sharpness, 0.0)
    if sharpness < settings.blur_variance_threshold:
        return FaceQualityResult(False, "blurred", fraction, sharpness, 0.0)
    score = (
        detection_confidence
        + min(1.0, fraction / 0.35)
        + min(1.0, sharpness / (settings.blur_variance_threshold * 4.0))
    ) / 3.0
    return FaceQualityResult(True, "accepted", fraction, sharpness, float(score))


def embedding_inlier_indices(
    features: Sequence[np.ndarray], minimum_median_similarity: float
) -> list[int]:
    """Keep embeddings consistent with the majority, not merely a centroid."""
    if len(features) < 3:
        return list(range(len(features)))
    matrix = np.stack([_normalise(feature) for feature in features])
    similarities = matrix @ matrix.T
    medians = np.array(
        [np.median(np.delete(similarities[index], index)) for index in range(len(features))]
    )
    centre = float(np.median(medians))
    mad = float(np.median(np.abs(medians - centre)))
    robust_cutoff = centre - 3.0 * max(mad, 0.01)
    cutoff = max(minimum_median_similarity, robust_cutoff)
    return [index for index, score in enumerate(medians) if float(score) >= cutoff]


def select_diverse_observations(
    observations: Sequence[FaceObservation], settings: FaceRecognitionSettings
) -> tuple[list[FaceObservation], int, int]:
    if not observations:
        return [], 0, 0
    inlier_indices = embedding_inlier_indices(
        [observation.feature for observation in observations],
        settings.outlier_median_similarity,
    )
    inliers = [observations[index] for index in inlier_indices]
    outliers_removed = len(observations) - len(inliers)
    remaining = sorted(inliers, key=lambda item: item.quality, reverse=True)
    selected: list[FaceObservation] = []
    selected_ids: set[int] = set()

    def add_if_distinct(candidate: FaceObservation) -> bool:
        if selected and max(
            float(np.dot(candidate.feature, existing.feature)) for existing in selected
        ) >= settings.duplicate_similarity:
            return False
        selected.append(candidate)
        selected_ids.add(id(candidate))
        return True

    for pose in ("front", "left", "right"):
        for candidate in remaining:
            if candidate.pose == pose and add_if_distinct(candidate):
                break

    while len(selected) < settings.target_enrollment_templates:
        eligible = [
            candidate
            for candidate in remaining
            if id(candidate) not in selected_ids
            and (
                not selected
                or max(float(np.dot(candidate.feature, known.feature)) for known in selected)
                < settings.duplicate_similarity
            )
        ]
        if not eligible:
            break
        candidate = max(
            eligible,
            key=lambda item: (
                min(1.0 - float(np.dot(item.feature, known.feature)) for known in selected)
                if selected
                else 1.0
            )
            + 0.05 * item.quality,
        )
        selected.append(candidate)
        selected_ids.add(id(candidate))

    duplicates_removed = len(inliers) - len(selected)
    return selected, outliers_removed, duplicates_removed


def aggregate_identity_scores(
    session_id: str,
    query_features: Sequence[np.ndarray],
    candidates: Mapping[str, Sequence[np.ndarray]],
    settings: FaceRecognitionSettings,
) -> IdentityResult:
    valid_frames = len(query_features)
    if valid_frames < settings.minimum_valid_frames:
        return IdentityResult(
            session_id,
            None,
            0.0,
            utc_now(),
            IdentityStatus.NO_FACE,
            valid_frames=valid_frames,
        )
    if not candidates:
        return IdentityResult(
            session_id,
            None,
            0.0,
            utc_now(),
            IdentityStatus.UNKNOWN_USER,
            valid_frames=valid_frames,
        )

    frame_scores: list[dict[str, float]] = []
    for query in query_features:
        per_user: dict[str, float] = {}
        for user_id, templates in candidates.items():
            similarities = sorted(
                (float(np.dot(query, template)) for template in templates), reverse=True
            )
            top = similarities[: min(settings.template_top_k, len(similarities))]
            per_user[user_id] = float(np.mean(top))
        frame_scores.append(per_user)

    aggregate = {
        user_id: float(np.median([scores[user_id] for scores in frame_scores]))
        for user_id in candidates
    }
    ranked = sorted(aggregate.items(), key=lambda entry: entry[1], reverse=True)
    best_id, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = best_score - second_score
    votes = Counter(max(scores, key=scores.get) for scores in frame_scores)
    vote_ratio = votes[best_id] / valid_frames

    if best_score < settings.absolute_threshold:
        status = IdentityStatus.UNKNOWN_USER
        matched_id = None
    elif margin < settings.margin_threshold or vote_ratio < settings.minimum_vote_ratio:
        status = IdentityStatus.AMBIGUOUS_USER
        matched_id = None
    else:
        status = IdentityStatus.MATCHED
        matched_id = best_id
    return IdentityResult(
        session_id=session_id,
        user_id=matched_id,
        confidence=max(0.0, best_score),
        observed_at=utc_now(),
        status=status,
        second_score=max(0.0, second_score),
        margin=margin,
        valid_frames=valid_frames,
        vote_ratio=vote_ratio,
    )


def _normalise(feature: np.ndarray) -> np.ndarray:
    feature = np.asarray(feature, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(feature))
    return feature / norm if norm > 0 else feature


class SFaceIdentityProvider:
    """Local YuNet detection plus SFace embeddings; no image leaves memory."""

    def __init__(
        self,
        detector_path: str | Path,
        recognizer_path: str | Path,
        settings: FaceRecognitionSettings,
    ) -> None:
        import cv2

        detector_path = Path(detector_path)
        recognizer_path = Path(recognizer_path)
        if not detector_path.is_file() or not recognizer_path.is_file():
            raise FileNotFoundError(
                "YuNet/SFace models are missing. Run: uv run python scripts/download_models.py"
            )
        settings.validate()
        self.cv2 = cv2
        self.settings = settings
        self.minimum_enrollment_templates = settings.minimum_enrollment_templates
        detector_buffer = np.frombuffer(detector_path.read_bytes(), dtype=np.uint8)
        recognizer_buffer = np.frombuffer(recognizer_path.read_bytes(), dtype=np.uint8)
        empty_config = np.empty(0, dtype=np.uint8)
        self.detector = cv2.FaceDetectorYN.create(
            "ONNX",
            detector_buffer,
            empty_config,
            (320, 320),
            settings.detector_threshold,
            0.3,
            5000,
        )
        self.recognizer = cv2.FaceRecognizerSF.create(
            "ONNX", recognizer_buffer, empty_config
        )

    def detect_for_preview(self, frame) -> list[tuple[int, int, int, int, float]]:
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

    @staticmethod
    def _pose(face: Any) -> str:
        eye_midpoint = (float(face[4]) + float(face[6])) / 2.0
        eye_distance = max(abs(float(face[6]) - float(face[4])), 1.0)
        offset = (float(face[8]) - eye_midpoint) / eye_distance
        if offset < -0.10:
            return "left"
        if offset > 0.10:
            return "right"
        return "front"

    def _observations(self, frames: Sequence[FrameSample]) -> list[FaceObservation]:
        observations: list[FaceObservation] = []
        for sample in frames:
            frame = sample.image
            height, width = frame.shape[:2]
            faces = self._detect(frame)
            face_count = 0 if faces is None else len(faces)
            if face_count != 1:
                continue
            face = faces[0]
            aligned = self.recognizer.alignCrop(frame, face)
            gray = self.cv2.cvtColor(aligned, self.cv2.COLOR_BGR2GRAY)
            sharpness = float(self.cv2.Laplacian(gray, self.cv2.CV_64F).var())
            quality = assess_face_quality(
                face_count=face_count,
                face_width=int(face[2]),
                face_height=int(face[3]),
                frame_width=width,
                frame_height=height,
                sharpness=sharpness,
                detection_confidence=float(face[-1]),
                settings=self.settings,
            )
            if not quality.accepted:
                continue
            feature = _normalise(self.recognizer.feature(aligned))
            if float(np.linalg.norm(feature)) > 0:
                observations.append(FaceObservation(feature, self._pose(face), quality.score))
        return observations

    def extract_templates(
        self, session_id: str, frames: Sequence[FrameSample]
    ) -> FaceEnrollmentResult:
        self._validate_session(session_id, frames)
        observations = self._observations(frames)
        selected, outliers, duplicates = select_diverse_observations(
            observations, self.settings
        )
        return FaceEnrollmentResult(
            templates=tuple(observation.feature.tobytes() for observation in selected),
            candidate_frames=len(frames),
            valid_frames=len(observations),
            outliers_removed=outliers,
            duplicates_removed=duplicates,
        )

    def identify(
        self,
        session_id: str,
        frames: Sequence[FrameSample],
        candidates: Sequence[FaceTemplate],
    ) -> IdentityResult:
        self._validate_session(session_id, frames)
        observations = self._observations(frames)
        grouped: dict[str, list[np.ndarray]] = defaultdict(list)
        for template in candidates:
            grouped[template.user_id].append(
                _normalise(np.frombuffer(template.feature, dtype=np.float32))
            )
        return aggregate_identity_scores(
            session_id,
            [observation.feature for observation in observations],
            grouped,
            self.settings,
        )

    @staticmethod
    def _validate_session(session_id: str, frames: Sequence[FrameSample]) -> None:
        if not frames or any(frame.session_id != session_id for frame in frames):
            raise ValueError("IdentityProvider received frames from different sessions")
