from __future__ import annotations

from fridge_guardian.domain import IdentityResult, ItemResult, utc_now


class FakeIdentityProvider:
    def __init__(self, user_id: str | None = None, confidence: float = 0.95) -> None:
        self.user_id = user_id
        self.confidence = confidence

    def extract_templates(self, session_id, frames):
        return [b"face-1", b"face-2", b"face-3"]

    def identify(self, session_id, frames, candidates):
        return IdentityResult(session_id, self.user_id, self.confidence, utc_now())


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
