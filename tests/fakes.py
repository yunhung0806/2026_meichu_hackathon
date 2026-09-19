from __future__ import annotations

from fridge_guardian.domain import (
    FaceEnrollmentResult,
    IdentityResult,
    IdentityStatus,
    ItemResult,
    utc_now,
)


class FakeIdentityProvider:
    minimum_enrollment_templates = 3

    def __init__(
        self,
        user_id: str | None = None,
        confidence: float = 0.95,
        status: IdentityStatus | None = None,
    ) -> None:
        self.user_id = user_id
        self.confidence = confidence
        self.status = status

    def extract_templates(self, session_id, frames):
        return FaceEnrollmentResult(
            (b"face-1", b"face-2", b"face-3"), len(frames), len(frames)
        )

    def identify(self, session_id, frames, candidates):
        status = self.status or (
            IdentityStatus.MATCHED if self.user_id else IdentityStatus.UNKNOWN_USER
        )
        return IdentityResult(
            session_id,
            self.user_id,
            self.confidence,
            utc_now(),
            status,
            second_score=0.10,
            margin=self.confidence - 0.10,
            valid_frames=len(frames),
            vote_ratio=1.0,
        )


class FakeItemRecognizer:
    def __init__(self, item_id: str | None = None, confidence: float = 0.0) -> None:
        self.item_id = item_id
        self.confidence = confidence

    def extract_templates(self, session_id, frames):
        return [b"item-1", b"item-2", b"item-3"]

    def identify(self, session_id, frames, candidates):
        return ItemResult(session_id, self.item_id, self.confidence, utc_now())


class RecordingFeedback:
    def __init__(self) -> None:
        self.decisions = []

    def publish(self, decision):
        self.decisions.append(decision)
