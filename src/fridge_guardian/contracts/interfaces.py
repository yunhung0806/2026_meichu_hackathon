from __future__ import annotations

from typing import Protocol, Sequence

from fridge_guardian.domain import (
    Action,
    Decision,
    FaceTemplate,
    FrameSample,
    IdentityResult,
    InteractionEvent,
    Item,
    ItemResult,
    ItemTemplate,
    User,
)


class ActionSource(Protocol):
    def action_for_key(self, key_code: int) -> Action | None: ...


class IdentityProvider(Protocol):
    def extract_templates(self, session_id: str, frames: Sequence[FrameSample]) -> list[bytes]: ...

    def identify(
        self,
        session_id: str,
        frames: Sequence[FrameSample],
        candidates: Sequence[FaceTemplate],
    ) -> IdentityResult: ...


class ItemRecognizer(Protocol):
    def extract_templates(self, session_id: str, frames: Sequence[FrameSample]) -> list[bytes]: ...

    def identify(
        self,
        session_id: str,
        frames: Sequence[FrameSample],
        candidates: Sequence[ItemTemplate],
    ) -> ItemResult: ...


class Repository(Protocol):
    def add_user(self, display_name: str) -> User: ...

    def list_users(self) -> list[User]: ...

    def add_face_templates(self, user_id: str, features: Sequence[bytes], feature_kind: str) -> None: ...

    def list_face_templates(self) -> list[FaceTemplate]: ...

    def add_item(self, owner_id: str, label: str) -> Item: ...

    def get_item(self, item_id: str) -> Item | None: ...

    def list_item_templates(self) -> list[ItemTemplate]: ...

    def add_item_templates(self, item_id: str, features: Sequence[bytes], feature_kind: str) -> None: ...

    def is_shared_with(self, item_id: str, user_id: str) -> bool: ...

    def record_event(self, event: InteractionEvent) -> None: ...


class Feedback(Protocol):
    def publish(self, decision: Decision) -> None: ...
