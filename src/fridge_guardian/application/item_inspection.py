"""Two-stage item inspection and confirmed inventory mutation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from secrets import token_urlsafe
from typing import Any, Sequence
from uuid import uuid4

import numpy as np

from fridge_guardian.adapters.item import ITEM_ROI
from fridge_guardian.adapters.item_vision import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_KIND,
    ROI_EMBEDDING_KIND,
)
from fridge_guardian.application.fridge_service import OperationResult
from fridge_guardian.domain import (
    Action,
    Decision,
    DecisionCode,
    FrameSample,
    IdentityResult,
    IdentityStatus,
    InteractionEvent,
)


INSPECTION_TTL_SECONDS = 120
LOGGER = logging.getLogger(__name__)


class InspectionError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass
class PendingInspection:
    inspection_id: str
    action: Action
    user_id: str
    session_id: str
    identity: IdentityResult
    localization: dict[str, Any]
    category: dict[str, Any]
    instance_status: str
    candidates: tuple[dict[str, Any], ...]
    authorized_inventory: tuple[dict[str, Any], ...]
    crop_embedding: bytes | None
    roi_embedding: bytes | None
    suggested_label: str
    created_at: datetime
    expires_at: datetime
    committable: bool
    review_decision: DecisionCode | None = None
    review_message: str | None = None
    consumed: bool = False


class ItemInspectionManager:
    """Keeps AI inspection state in memory until an explicit confirmed commit."""

    def __init__(self, service: Any, client: Any, *, ttl_seconds: int = INSPECTION_TTL_SECONDS):
        self.service = service
        self.repo = service.repo
        self.client = client
        self.ttl = timedelta(seconds=ttl_seconds)
        self.pending: dict[str, PendingInspection] = {}

    def inspect(
        self, token: str, action: Action, frames: Sequence[FrameSample]
    ) -> dict[str, Any]:
        login = self.service._login(token)
        action = Action(action)
        session_id = self.service.coordinator._session_id(frames)
        identity = self.service.coordinator.identity_provider.identify(
            session_id, frames, self.repo.list_face_templates()
        )
        if (
            identity.session_id != session_id
            or identity.status is not IdentityStatus.MATCHED
            or identity.user_id != login.user_id
        ):
            raise InspectionError(
                "PERSON_CHANGED",
                "The face check did not match the signed-in user. Please rescan.",
                403,
            )

        roi = self._item_roi(frames[len(frames) // 2].image)
        request_id = token_urlsafe(18)
        result = self.client.infer(
            roi, self._gallery(action, login.user_id), request_id=request_id
        )
        localization = dict(result["localization"])
        category = dict(result["category"])
        instance = dict(result["instance"])
        embeddings = result.get("embeddings")
        model_result_committable = (
            localization.get("status") == "OK" and embeddings is not None
        )

        eligible_candidates = self._eligible_candidates(
            action, login.user_id, instance.get("candidates", [])
        )
        private_owner_match = self._private_owner_match(
            action, login.user_id, instance
        )
        authorized_inventory = (
            tuple(self._authorized_inventory(login.user_id))
            if action is Action.TAKE_OUT
            else ()
        )
        committable = model_result_committable and (
            action is Action.PUT_IN or bool(authorized_inventory)
        )
        suggested_label = self._suggested_label(category, eligible_candidates)
        now = self.service.clock()
        inspection_id = token_urlsafe(24)
        pending = PendingInspection(
            inspection_id=inspection_id,
            action=action,
            user_id=login.user_id,
            session_id=session_id,
            identity=identity,
            localization=localization,
            category=category,
            instance_status=str(instance.get("status")),
            candidates=tuple(eligible_candidates),
            authorized_inventory=authorized_inventory,
            crop_embedding=self._embedding_bytes(embeddings, "crop") if embeddings else None,
            roi_embedding=self._embedding_bytes(embeddings, "roi") if embeddings else None,
            suggested_label=suggested_label,
            created_at=now,
            expires_at=now + self.ttl,
            committable=committable,
            review_decision=(
                DecisionCode.WARN_NOT_OWNER if private_owner_match else None
            ),
            review_message=(
                "WARN_NOT_OWNER: AI matched a private item owned by another user. It cannot be "
                "removed. If the AI result is wrong, choose another authorized item."
                if private_owner_match
                else None
            ),
        )
        self._discard_expired(now)
        self.pending[inspection_id] = pending
        if private_owner_match:
            self._publish_private_owner_warning(
                pending, private_owner_match
            )
        return self._public(pending, login.display_name, result.get("latency_ms", {}))

    def commit(
        self,
        token: str,
        inspection_id: str,
        action: Action,
        *,
        confirmed: bool,
        label: str | None,
        selected_item_id: str | None,
        add_as_new: bool,
        shared: bool,
        expires_on: date | None,
        shared_user_ids: Sequence[str] = (),
    ) -> OperationResult:
        login = self.service._login(token)
        pending = self.pending.get(inspection_id)
        if pending is None:
            raise InspectionError("INSPECTION_NOT_FOUND", "Inspection is unknown or expired.", 404)
        now = self.service.clock()
        if pending.expires_at <= now:
            self.pending.pop(inspection_id, None)
            raise InspectionError("INSPECTION_EXPIRED", "Inspection expired. Please rescan.", 409)
        if pending.consumed:
            raise InspectionError("INSPECTION_USED", "Inspection was already committed.", 409)
        if not confirmed:
            raise InspectionError("CONFIRMATION_REQUIRED", "Explicit confirmation is required.")
        if pending.action is not Action(action):
            raise InspectionError("ACTION_MISMATCH", "The confirmed action does not match the inspection.", 409)
        if pending.user_id != login.user_id:
            raise InspectionError("IDENTITY_MISMATCH", "Inspection belongs to another signed-in user.", 403)
        if (
            pending.action is Action.TAKE_OUT
            and pending.localization.get("status") == "OK"
            and pending.crop_embedding is not None
            and not pending.authorized_inventory
        ):
            if selected_item_id is not None:
                raise InspectionError(
                    "INVALID_ITEM_SELECTION",
                    "The selected item is not authorized for this inspection.",
                    403,
                )
            raise InspectionError(
                "NO_AUTHORIZED_ITEMS",
                "No authorized present inventory item is available. Nothing was changed.",
                409,
            )
        if not pending.committable:
            raise InspectionError("RESCAN_REQUIRED", "The item could not be localized. Please rescan.", 409)

        if pending.action is Action.PUT_IN:
            result = self._commit_put(
                pending,
                login.display_name,
                label,
                selected_item_id,
                add_as_new,
                shared,
                expires_on,
                shared_user_ids,
            )
        else:
            result = self._commit_take(pending, selected_item_id)
        pending.consumed = True
        try:
            self.service.coordinator._finish(result.decision)
        except Exception:
            # The inventory transaction already committed. Return the truthful
            # mutation result and surface a warning instead of inviting a
            # dangerous retry that could apply the operation twice.
            LOGGER.exception("Post-commit event or feedback failed")
            return OperationResult(
                result.decision,
                result.warnings + ("POST_COMMIT_EVENT_OR_FEEDBACK_FAILED",),
            )
        return result

    def _commit_put(
        self,
        pending: PendingInspection,
        display_name: str,
        label: str | None,
        selected_item_id: str | None,
        add_as_new: bool,
        shared: bool,
        expires_on: date | None,
        shared_user_ids: Sequence[str],
    ) -> OperationResult:
        clean_label = (label or "").strip()
        if not clean_label or len(clean_label) > 200:
            raise InspectionError("VALIDATION_ERROR", "A reviewed item name of 1–200 characters is required.")
        if type(shared) is not bool:
            raise InspectionError("VALIDATION_ERROR", "shared must be a boolean.")
        if expires_on is not None and type(expires_on) is not date:
            raise InspectionError("VALIDATION_ERROR", "expires_on must be a date or null.")
        share_ids = self._validate_share_users(
            pending.user_id, shared, shared_user_ids
        )

        allowed = {candidate["item_id"] for candidate in pending.candidates}
        status = pending.instance_status
        if status == "AMBIGUOUS" and not add_as_new and not selected_item_id:
            raise InspectionError("ITEM_SELECTION_REQUIRED", "Choose a stored item or add this as a new item.")
        if status == "NO_MATCH" and selected_item_id is None:
            add_as_new = True
        if (
            status == "MATCHED"
            and not add_as_new
            and selected_item_id is None
            and pending.candidates
        ):
            selected_item_id = pending.candidates[0]["item_id"]
        if selected_item_id is not None and selected_item_id not in allowed:
            raise InspectionError("INVALID_ITEM_SELECTION", "The selected item was not offered by this inspection.", 403)
        if add_as_new and selected_item_id is not None:
            raise InspectionError("INVALID_ITEM_SELECTION", "Choose either a stored item or a new item, not both.")
        if status == "MATCHED" and not add_as_new and selected_item_id is None:
            raise InspectionError("ITEM_SELECTION_REQUIRED", "The matched item is not eligible to be put in.")

        item_id = str(uuid4()) if add_as_new or selected_item_id is None else selected_item_id
        if not add_as_new:
            self._require_put_eligible(item_id, pending.user_id)
        now = self.service.clock().isoformat()
        assert pending.crop_embedding is not None and pending.roi_embedding is not None
        with self.repo.connection:
            if add_as_new or self.repo.get_item(item_id) is None:
                self.repo.connection.execute(
                    "INSERT INTO items(item_id, owner_id, label, created_at) VALUES (?, ?, ?, ?)",
                    (item_id, pending.user_id, clean_label, now),
                )
            else:
                self.repo.connection.execute(
                    "UPDATE items SET label=? WHERE item_id=?", (clean_label, item_id)
                )
            self.repo.connection.executemany(
                "INSERT INTO item_templates VALUES (?, ?, ?, ?, ?)",
                [
                    (str(uuid4()), item_id, pending.crop_embedding, EMBEDDING_KIND, now),
                    (str(uuid4()), item_id, pending.roi_embedding, ROI_EMBEDDING_KIND, now),
                ],
            )
            self.repo.connection.execute(
                """INSERT INTO inventory VALUES (?, ?, ?, ?, 1)
                   ON CONFLICT(item_id) DO UPDATE SET shared=excluded.shared,
                   put_at=excluded.put_at, expires_on=excluded.expires_on, present=1""",
                (item_id, int(shared), now, expires_on.isoformat() if expires_on else None),
            )
            self.repo.connection.execute(
                "DELETE FROM item_shares WHERE item_id=?", (item_id,)
            )
            self.repo.connection.executemany(
                "INSERT INTO item_shares(item_id, user_id, created_at) VALUES (?, ?, ?)",
                [(item_id, user_id, now) for user_id in share_ids],
            )
        decision = self._decision(
            pending,
            DecisionCode.ITEM_REGISTERED,
            item_id,
            f"Stored {clean_label} for {display_name}",
        )
        return OperationResult(decision)

    def _commit_take(
        self, pending: PendingInspection, selected_item_id: str | None
    ) -> OperationResult:
        status = pending.instance_status
        allowed = {item["item_id"] for item in pending.authorized_inventory}
        if status == "MATCHED" and selected_item_id is None and pending.candidates:
            selected_item_id = pending.candidates[0]["item_id"]
        if status == "AMBIGUOUS" and selected_item_id is None:
            raise InspectionError("ITEM_SELECTION_REQUIRED", "Choose one of the authorized matching items.")
        if status == "NO_MATCH" and selected_item_id is None:
            raise InspectionError("ITEM_SELECTION_REQUIRED", "Choose an authorized inventory item.")
        if selected_item_id is None or selected_item_id not in allowed:
            raise InspectionError("INVALID_ITEM_SELECTION", "The selected item is not authorized for this inspection.", 403)
        item, state = self._present_item(selected_item_id)
        if item is None or state is None or not self._authorized(item.owner_id, state, pending.user_id, selected_item_id):
            raise InspectionError("NOT_AUTHORIZED", "This item is no longer available to this user.", 403)
        warnings: list[str] = []
        if state["expires_on"] and date.fromisoformat(state["expires_on"]) < self.service._today():
            warnings.append("EXPIRED: recorded package date has passed")
        with self.repo.connection:
            self.repo.connection.execute(
                "UPDATE inventory SET present=0 WHERE item_id=? AND present=1",
                (selected_item_id,),
            )
        code = DecisionCode.ALLOW_OWNER if item.owner_id == pending.user_id else DecisionCode.ALLOW_SHARED
        message = "Take-out recorded"
        if warnings:
            message += "; " + "; ".join(warnings)
        return OperationResult(
            self._decision(pending, code, selected_item_id, message, tuple(warnings)),
            tuple(warnings),
        )

    def _decision(
        self,
        pending: PendingInspection,
        code: DecisionCode,
        item_id: str,
        message: str,
        warnings: tuple[str, ...] = (),
    ) -> Decision:
        score = next(
            (float(entry["similarity"]) for entry in pending.candidates if entry["item_id"] == item_id),
            0.0,
        )
        identity = pending.identity
        return Decision(
            session_id=pending.session_id,
            action=pending.action,
            code=code,
            message=message,
            user_id=pending.user_id,
            item_id=item_id,
            identity_confidence=identity.confidence,
            identity_status=identity.status,
            identity_second_score=identity.second_score,
            identity_margin=identity.margin,
            identity_valid_frames=identity.valid_frames,
            identity_vote_ratio=identity.vote_ratio,
            item_confidence=score,
            warnings=warnings,
        )

    def _gallery(self, action: Action, user_id: str) -> list[dict[str, object]]:
        if action is Action.PUT_IN:
            relevant_ids = {
                row["item_id"]
                for row in self.repo.connection.execute(
                    "SELECT item_id FROM items WHERE owner_id=?", (user_id,)
                )
            }
        else:
            relevant_ids = {
                row["item_id"]
                for row in self.repo.connection.execute(
                    "SELECT item_id FROM inventory WHERE present=1"
                )
            }
        crops: dict[str, list[np.ndarray]] = {}
        rois: dict[str, list[np.ndarray]] = {}
        for template in self.repo.list_item_templates():
            if template.item_id not in relevant_ids:
                continue
            if template.feature_kind == EMBEDDING_KIND:
                crops.setdefault(template.item_id, []).append(self._vector(template.feature))
            elif template.feature_kind == ROI_EMBEDDING_KIND:
                rois.setdefault(template.item_id, []).append(self._vector(template.feature))
        gallery: list[dict[str, object]] = []
        for item_id, crop_values in crops.items():
            roi_values = rois.get(item_id, [])
            for index, crop in enumerate(crop_values):
                roi = roi_values[min(index, len(roi_values) - 1)] if roi_values else crop
                gallery.append(
                    {"item_id": item_id, "crop_embedding": crop.tolist(), "roi_embedding": roi.tolist()}
                )
                if len(gallery) == 100:
                    return gallery
        return gallery

    def _eligible_candidates(
        self, action: Action, user_id: str, raw: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        eligible: list[dict[str, Any]] = []
        user_names = {user.user_id: user.display_name for user in self.repo.list_users()}
        for candidate in raw:  # Preserve sidecar order; the backend never reranks.
            item_id = str(candidate["item_id"])
            item, state = self._present_item(item_id)
            if item is None:
                continue
            if action is Action.PUT_IN:
                allowed = item.owner_id == user_id and (state is None or not state["present"])
            else:
                allowed = state is not None and self._authorized(
                    item.owner_id, state, user_id, item_id
                )
            if allowed:
                eligible.append(
                    {
                        "item_id": item_id,
                        "label": item.label,
                        "similarity": float(candidate["similarity"]),
                        "owner_id": item.owner_id,
                        "owner_display_name": user_names.get(item.owner_id, "Unknown"),
                        "owner_name": user_names.get(item.owner_id, "Unknown"),
                        "shared": bool(
                            state is not None
                            and (
                                state["shared"]
                                or self.repo.is_shared_with(item_id, user_id)
                            )
                        ),
                        "put_at": state["put_at"] if state is not None else None,
                        "expires_on": state["expires_on"] if state is not None else None,
                    }
                )
        return eligible

    def _private_owner_match(
        self,
        action: Action,
        user_id: str,
        instance: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return a top-ranked private-owner match without exposing it as a choice.

        An AMBIGUOUS result can still be a useful safety signal: duplicate-looking
        items often make the instance margin too small for MATCHED, but the
        highest-ranked candidate may still be another user's private present item.
        The warning never authorizes or mutates that item.
        """
        if action is not Action.TAKE_OUT or instance.get("status") not in {
            "MATCHED",
            "AMBIGUOUS",
        }:
            return None
        candidates = instance.get("candidates", [])
        if not candidates:
            return None
        candidate = candidates[0]
        item_id = str(candidate.get("item_id", ""))
        item, state = self._present_item(item_id)
        if (
            item is None
            or state is None
            or not state["present"]
            or self._authorized(item.owner_id, state, user_id, item_id)
        ):
            return None
        return {"item_id": item_id, "similarity": float(candidate["similarity"])}

    def _publish_private_owner_warning(
        self,
        pending: PendingInspection,
        candidate: dict[str, Any],
    ) -> None:
        identity = pending.identity
        decision = Decision(
                    session_id=pending.session_id,
                    action=Action.TAKE_OUT,
                    code=DecisionCode.WARN_NOT_OWNER,
                    message=pending.review_message or "This item belongs to another user.",
                    user_id=pending.user_id,
                    item_id=str(candidate["item_id"]),
                    identity_confidence=identity.confidence,
                    identity_status=identity.status,
                    identity_second_score=identity.second_score,
                    identity_margin=identity.margin,
                    identity_valid_frames=identity.valid_frames,
                    identity_vote_ratio=identity.vote_ratio,
                    item_confidence=float(candidate["similarity"]),
                )
        try:
            self._record_warning_for_actor_and_owner(decision)
        except Exception:
            LOGGER.exception("WARN_NOT_OWNER event recording failed")
        try:
            self.service.coordinator.feedback.publish(decision)
        except Exception:
            LOGGER.exception("WARN_NOT_OWNER feedback failed")

    def _record_warning_for_actor_and_owner(self, decision: Decision) -> None:
        """Record a denied take-out for both actor and owner without mutation."""
        item = self.repo.get_item(decision.item_id or "")
        user_ids = [decision.user_id]
        if item is not None and item.owner_id != decision.user_id:
            user_ids.append(item.owner_id)
        for user_id in user_ids:
            if user_id is None:
                continue
            self.repo.record_event(
                InteractionEvent(
                    event_id=str(uuid4()),
                    session_id=decision.session_id,
                    action=decision.action,
                    decision=decision.code,
                    occurred_at=decision.decided_at,
                    user_id=user_id,
                    item_id=decision.item_id,
                    identity_confidence=decision.identity_confidence,
                    identity_status=decision.identity_status,
                    identity_second_score=decision.identity_second_score,
                    identity_margin=decision.identity_margin,
                    identity_valid_frames=decision.identity_valid_frames,
                    identity_vote_ratio=decision.identity_vote_ratio,
                    item_confidence=decision.item_confidence,
                )
            )

    def _validate_share_users(
        self,
        owner_id: str,
        shared: bool,
        shared_user_ids: Sequence[str],
    ) -> tuple[str, ...]:
        if isinstance(shared_user_ids, (str, bytes)):
            raise InspectionError(
                "VALIDATION_ERROR", "shared_user_ids must be a list."
            )
        values = tuple(dict.fromkeys(shared_user_ids))
        if shared and values:
            raise InspectionError(
                "VALIDATION_ERROR",
                "Choose either all-member sharing or specific members, not both.",
            )
        if owner_id in values:
            raise InspectionError(
                "VALIDATION_ERROR", "The owner cannot be added as a share recipient."
            )
        known = {user.user_id for user in self.repo.list_users()}
        if any(not isinstance(value, str) or value not in known for value in values):
            raise InspectionError(
                "INVALID_SHARE_SELECTION",
                "One or more selected sharing users are invalid.",
                403,
            )
        return values

    def _authorized_inventory(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.repo.connection.execute(
            """SELECT i.item_id, i.label, i.owner_id,
                      u.display_name AS owner_display_name,
                      u.display_name AS owner_name,
                      CASE WHEN s.shared=1 OR EXISTS (
                        SELECT 1 FROM item_shares any_share
                        WHERE any_share.item_id=i.item_id
                      ) THEN 1 ELSE 0 END AS shared,
                      s.put_at, s.expires_on
               FROM inventory s
               JOIN items i USING(item_id)
               JOIN users u ON u.user_id=i.owner_id
               WHERE s.present=1 AND (
                 i.owner_id=? OR s.shared=1 OR EXISTS (
                   SELECT 1 FROM item_shares x WHERE x.item_id=i.item_id AND x.user_id=?
                 )
               )
               ORDER BY (s.expires_on IS NULL), s.expires_on, s.put_at, i.item_id""",
            (user_id, user_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def _require_put_eligible(self, item_id: str, user_id: str) -> None:
        item, state = self._present_item(item_id)
        if item is None or item.owner_id != user_id or (state is not None and state["present"]):
            raise InspectionError("INVALID_ITEM_SELECTION", "Stored item is not eligible for this put-in.", 403)

    def _present_item(self, item_id: str):
        item = self.repo.get_item(item_id)
        state = self.repo.connection.execute(
            "SELECT * FROM inventory WHERE item_id=?", (item_id,)
        ).fetchone()
        return item, state

    def _authorized(self, owner_id: str, state: Any, user_id: str, item_id: str) -> bool:
        return bool(
            state["present"]
            and (
                owner_id == user_id
                or state["shared"]
                or self.repo.is_shared_with(item_id, user_id)
            )
        )

    @staticmethod
    def _item_roi(frame: Any) -> Any:
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = ITEM_ROI
        roi = frame[
            int(height * y1) : int(height * y2),
            int(width * x1) : int(width * x2),
        ].copy()
        if getattr(roi, "size", 0) == 0:
            raise InspectionError("INVALID_ROI", "The configured item ROI is empty.")
        return roi

    @staticmethod
    def _vector(feature: bytes) -> np.ndarray:
        value = np.frombuffer(feature, dtype=np.float32).copy()
        if value.size != EMBEDDING_DIMENSIONS or not np.isfinite(value).all():
            raise ValueError("Stored DINOv2 embedding is invalid")
        norm = float(np.linalg.norm(value))
        if norm <= 1e-12:
            raise ValueError("Stored DINOv2 embedding has zero norm")
        return (value / norm).astype(np.float32)

    @classmethod
    def _embedding_bytes(cls, embeddings: dict[str, Any], key: str) -> bytes:
        return cls._vector(np.asarray(embeddings[key], dtype=np.float32).tobytes()).tobytes()

    @staticmethod
    def _suggested_label(category: dict[str, Any], candidates: Sequence[dict[str, Any]]) -> str:
        if candidates:
            return str(candidates[0]["label"])
        top3 = category.get("top3", [])
        return str(top3[0]["label"]) if category.get("status") == "OK" and top3 else ""

    @staticmethod
    def _public(
        pending: PendingInspection, display_name: str, latency: dict[str, Any]
    ) -> dict[str, Any]:
        no_authorized_take_choices = (
            pending.action is Action.TAKE_OUT and not pending.authorized_inventory
        )
        return {
            "inspection_id": pending.inspection_id,
            "action": pending.action.value,
            "expires_at": pending.expires_at.isoformat(),
            "identity": {
                "status": pending.identity.status.value,
                "user_id": pending.user_id,
                "display_name": display_name,
            },
            "localization": {
                "status": pending.localization.get("status"),
                "score": pending.localization.get("score", 0.0),
                "box": pending.localization.get("box"),
            },
            "category": {
                "status": pending.category.get("status"),
                "top3": pending.category.get("top3", []),
            },
            "instance": {
                "status": pending.instance_status,
                "candidates": list(pending.candidates),
            },
            "suggested_label": pending.suggested_label,
            "authorized_inventory": list(pending.authorized_inventory),
            "committable": pending.committable,
            "review_state": (
                pending.review_decision.value
                if pending.review_decision is not None
                else "NO_AUTHORIZED_ITEMS"
                if no_authorized_take_choices
                else "READY"
            ),
            "review_decision": (
                pending.review_decision.value
                if pending.review_decision is not None
                else None
            ),
            "review_message": pending.review_message or (
                "No authorized present inventory item is available. Nothing will be changed."
                if no_authorized_take_choices
                else None
            ),
            "latency_ms": latency,
        }

    def _discard_expired(self, now: datetime) -> None:
        self.pending = {
            key: value
            for key, value in self.pending.items()
            if value.expires_at > now
        }
