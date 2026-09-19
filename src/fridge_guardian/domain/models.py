from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Action(str, Enum):
    PUT_IN = "PUT_IN"
    TAKE_OUT = "TAKE_OUT"


class DecisionCode(str, Enum):
    ITEM_REGISTERED = "ITEM_REGISTERED"
    ITEM_ALREADY_REGISTERED = "ITEM_ALREADY_REGISTERED"
    ALLOW_OWNER = "ALLOW_OWNER"
    ALLOW_SHARED = "ALLOW_SHARED"
    WARN_NOT_OWNER = "WARN_NOT_OWNER"
    UNKNOWN_USER = "UNKNOWN_USER"
    UNKNOWN_ITEM = "UNKNOWN_ITEM"


@dataclass(frozen=True)
class User:
    user_id: str
    display_name: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class Item:
    item_id: str
    owner_id: str
    label: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class FrameSample:
    session_id: str
    captured_at: datetime
    image: Any


@dataclass(frozen=True)
class FaceTemplate:
    user_id: str
    feature: bytes
    feature_kind: str = "sface-f32-v1"


@dataclass(frozen=True)
class ItemTemplate:
    item_id: str
    feature: bytes
    feature_kind: str = "spatial-hsv-f32-v1"


@dataclass(frozen=True)
class IdentityResult:
    session_id: str
    user_id: str | None
    confidence: float
    observed_at: datetime


@dataclass(frozen=True)
class ItemResult:
    session_id: str
    item_id: str | None
    confidence: float
    observed_at: datetime


@dataclass(frozen=True)
class Decision:
    session_id: str
    action: Action
    code: DecisionCode
    message: str
    user_id: str | None = None
    item_id: str | None = None
    identity_confidence: float = 0.0
    item_confidence: float = 0.0
    decided_at: datetime = field(default_factory=utc_now)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class InteractionEvent:
    event_id: str
    session_id: str
    action: Action
    decision: DecisionCode
    occurred_at: datetime
    user_id: str | None = None
    item_id: str | None = None
    identity_confidence: float = 0.0
    item_confidence: float = 0.0
