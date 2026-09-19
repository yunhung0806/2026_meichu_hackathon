"""Strict loopback client for the item-vision-v1 sidecar."""

from __future__ import annotations

import base64
import json
import math
import re
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


PROTOCOL_VERSION = "item-vision-v1"
EMBEDDING_KIND = "dinov2-vits14-crop-f32-v1"
ROI_EMBEDDING_KIND = "dinov2-vits14-roi-f32-v1"
EMBEDDING_DIMENSIONS = 384
INSTANCE_STATUSES = {"MATCHED", "AMBIGUOUS", "NO_MATCH", "NOT_RUN"}
CATEGORY_STATUSES = {"OK", "UNKNOWN_CATEGORY", "NOT_RUN"}
ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ItemVisionClientError(RuntimeError):
    """The local item-vision service is unavailable or violated its contract."""


class ItemVisionClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765", timeout: float = 60.0):
        self.base_url = _loopback_url(base_url)
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("Item vision timeout must be positive")

    def health(self) -> dict[str, Any]:
        result = self._request("/health", method="GET")
        _require_protocol(result)
        if result.get("status") != "ready" or not isinstance(result.get("models"), dict):
            raise ItemVisionClientError("Malformed item vision health response")
        return result

    def infer(
        self,
        roi_bgr: Any,
        gallery: list[dict[str, object]],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        import cv2

        ok, encoded = cv2.imencode(".jpg", roi_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise ItemVisionClientError("Could not encode the item ROI as JPEG")
        payload = {
            "request_id": request_id,
            "roi_jpeg_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
            "gallery": gallery,
        }
        result = self._request("/infer", method="POST", payload=payload)
        _validate_infer_response(result, request_id)
        return result

    def _request(
        self, path: str, *, method: str, payload: dict[str, object] | None = None
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except (TimeoutError, socket.timeout) as exc:
            raise ItemVisionClientError("Item vision request timed out") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ItemVisionClientError(f"Item vision service unavailable: {exc}") from exc
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ItemVisionClientError("Item vision service returned malformed JSON") from exc
        if not isinstance(result, dict):
            raise ItemVisionClientError("Item vision service returned a non-object response")
        if "error" in result:
            raise ItemVisionClientError(f"Item vision service error: {result['error']}")
        return result


def _loopback_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Item vision URL must use an HTTP loopback address")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("Item vision URL must not contain a path, query, or fragment")
    return value.rstrip("/")


def _require_protocol(payload: dict[str, Any]) -> None:
    version = payload.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise ItemVisionClientError(
            f"Incompatible item vision protocol: expected {PROTOCOL_VERSION}, got {version!r}"
        )


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_infer_response(result: dict[str, Any], request_id: str) -> None:
    _require_protocol(result)
    required = {"localization", "category", "instance", "roi_instance", "embeddings", "models", "latency_ms"}
    missing = required - result.keys()
    if missing:
        raise ItemVisionClientError(f"Item vision response is missing: {', '.join(sorted(missing))}")
    if result.get("request_id") != request_id:
        raise ItemVisionClientError("Item vision response request_id does not match")
    category = result.get("category")
    if not isinstance(category, dict) or category.get("status") not in CATEGORY_STATUSES:
        raise ItemVisionClientError("Malformed category result")
    top3 = category.get("top3")
    if not isinstance(top3, list) or len(top3) > 3:
        raise ItemVisionClientError("Malformed category top3")
    for suggestion in top3:
        if (
            not isinstance(suggestion, dict)
            or not isinstance(suggestion.get("label"), str)
            or not _finite_number(suggestion.get("score"))
        ):
            raise ItemVisionClientError("Malformed category suggestion")
    for key in ("instance", "roi_instance"):
        match = result.get(key)
        if not isinstance(match, dict) or match.get("status") not in INSTANCE_STATUSES:
            raise ItemVisionClientError(f"Malformed {key} result")
        candidates = match.get("candidates")
        if not isinstance(candidates, list) or len(candidates) > 3:
            raise ItemVisionClientError(f"Malformed {key} candidates")
        previous = math.inf
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ItemVisionClientError(f"Malformed {key} candidate")
            item_id = candidate.get("item_id")
            similarity = candidate.get("similarity")
            if not isinstance(item_id, str) or not ITEM_ID_PATTERN.fullmatch(item_id):
                raise ItemVisionClientError(f"Malformed {key} candidate item_id")
            if not _finite_number(similarity) or float(similarity) > previous:
                raise ItemVisionClientError(f"Malformed or unordered {key} candidates")
            previous = float(similarity)
    embeddings = result.get("embeddings")
    if embeddings is None:
        return
    if (
        not isinstance(embeddings, dict)
        or embeddings.get("kind") != EMBEDDING_KIND
        or embeddings.get("dimensions") != EMBEDDING_DIMENSIONS
    ):
        raise ItemVisionClientError("Malformed item vision embedding metadata")
    for key in ("crop", "roi"):
        values = embeddings.get(key)
        if (
            not isinstance(values, list)
            or len(values) != EMBEDDING_DIMENSIONS
            or any(not _finite_number(value) for value in values)
        ):
            raise ItemVisionClientError(f"Malformed {key} embedding")
