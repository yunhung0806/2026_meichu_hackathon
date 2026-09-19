from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

from fridge_guardian.application.policy import ownership_decision
from fridge_guardian.contracts import Feedback, IdentityProvider, ItemRecognizer, Repository
from fridge_guardian.domain import (
    Action,
    Decision,
    DecisionCode,
    FrameSample,
    IdentityResult,
    IdentityStatus,
    InteractionEvent,
    User,
    utc_now,
)


class EnrollmentError(RuntimeError):
    pass


class SessionCoordinator:
    """Combines independent face and item results from one capture session."""

    def __init__(
        self,
        repository: Repository,
        identity_provider: IdentityProvider,
        item_recognizer: ItemRecognizer,
        feedback: Feedback,
    ) -> None:
        self.repository = repository
        self.identity_provider = identity_provider
        self.item_recognizer = item_recognizer
        self.feedback = feedback

    @staticmethod
    def _session_id(frames: Sequence[FrameSample]) -> str:
        if not frames:
            raise ValueError("A session requires at least one frame")
        session_ids = {frame.session_id for frame in frames}
        if len(session_ids) != 1:
            raise ValueError("Face and item frames must belong to one session_id")
        return frames[0].session_id

    def enroll_user(self, display_name: str, frames: Sequence[FrameSample]) -> User:
        session_id = self._session_id(frames)
        clean_name = display_name.strip()
        if not clean_name:
            raise EnrollmentError("Display name cannot be empty")
        extraction = self.identity_provider.extract_templates(session_id, frames)
        required = self.identity_provider.minimum_enrollment_templates
        if len(extraction.templates) < required:
            raise EnrollmentError(
                f"Kept {len(extraction.templates)}/{extraction.candidate_frames} frames; "
                f"need {required}. Face camera, then turn slightly left and right."
            )
        user = self.repository.add_user(clean_name)
        self.repository.add_face_templates(user.user_id, extraction.templates, "sface-f32-v2")
        return user

    def process(self, action: Action, frames: Sequence[FrameSample]) -> Decision:
        session_id = self._session_id(frames)
        identity = self.identity_provider.identify(
            session_id, frames, self.repository.list_face_templates()
        )
        if identity.status is not IdentityStatus.MATCHED or identity.user_id is None:
            status = (
                identity.status
                if identity.status is not IdentityStatus.MATCHED
                else IdentityStatus.UNKNOWN_USER
            )
            codes = {
                IdentityStatus.NO_FACE: DecisionCode.NO_FACE,
                IdentityStatus.UNKNOWN_USER: DecisionCode.UNKNOWN_USER,
                IdentityStatus.AMBIGUOUS_USER: DecisionCode.AMBIGUOUS_USER,
            }
            messages = {
                IdentityStatus.NO_FACE: "No face - keep one clear face visible and retry",
                IdentityStatus.UNKNOWN_USER: "Unknown user - enroll with U or try again",
                IdentityStatus.AMBIGUOUS_USER: "Ambiguous user - face camera directly and retry",
            }
            return self._finish(
                Decision(
                    session_id=session_id,
                    action=action,
                    code=codes[status],
                    message=messages[status],
                    **self._identity_fields(identity),
                )
            )

        if action is Action.PUT_IN:
            return self._put_in(session_id, frames, identity)
        return self._take_out(session_id, frames, identity)

    def _put_in(
        self,
        session_id: str,
        frames: Sequence[FrameSample],
        identity: IdentityResult,
    ) -> Decision:
        assert identity.user_id is not None
        user_id = identity.user_id
        user_name = self._user_name(user_id)
        candidates = self.repository.list_item_templates()
        match = self.item_recognizer.identify(session_id, frames, candidates)
        if match.item_id is not None:
            return self._finish(
                Decision(
                    session_id=session_id,
                    action=Action.PUT_IN,
                    code=DecisionCode.ITEM_ALREADY_REGISTERED,
                    message=f"Known item {match.item_id[:8]} returned by {user_name}",
                    user_id=user_id,
                    item_id=match.item_id,
                    **self._identity_fields(identity),
                    item_confidence=match.confidence,
                )
            )

        features = self.item_recognizer.extract_templates(session_id, frames)
        if len(features) < 3:
            return self._finish(
                Decision(
                    session_id=session_id,
                    action=Action.PUT_IN,
                    code=DecisionCode.UNKNOWN_ITEM,
                    message="Item capture unclear - fill the green box and retry",
                    user_id=user_id,
                    **self._identity_fields(identity),
                )
            )

        item = self.repository.add_item(user_id, f"item-{uuid4().hex[:6]}")
        self.repository.add_item_templates(item.item_id, features, "spatial-hsv-f32-v1")
        return self._finish(
            Decision(
                session_id=session_id,
                action=Action.PUT_IN,
                code=DecisionCode.ITEM_REGISTERED,
                message=f"Registered {item.label} to {user_name}",
                user_id=user_id,
                item_id=item.item_id,
                **self._identity_fields(identity),
            )
        )

    def _take_out(
        self,
        session_id: str,
        frames: Sequence[FrameSample],
        identity: IdentityResult,
    ) -> Decision:
        assert identity.user_id is not None
        user_id = identity.user_id
        user_name = self._user_name(user_id)
        match = self.item_recognizer.identify(
            session_id, frames, self.repository.list_item_templates()
        )
        if match.item_id is None:
            return self._finish(
                Decision(
                    session_id=session_id,
                    action=Action.TAKE_OUT,
                    code=DecisionCode.UNKNOWN_ITEM,
                    message="Unknown item - no ownership decision made",
                    user_id=user_id,
                    **self._identity_fields(identity),
                    item_confidence=match.confidence,
                )
            )

        item = self.repository.get_item(match.item_id)
        if item is None:
            return self._finish(
                Decision(
                    session_id=session_id,
                    action=Action.TAKE_OUT,
                    code=DecisionCode.UNKNOWN_ITEM,
                    message="Item template has no ownership record",
                    user_id=user_id,
                    item_confidence=match.confidence,
                    **self._identity_fields(identity),
                )
            )
        code = ownership_decision(
            item,
            acting_user_id=user_id,
            shared=self.repository.is_shared_with(item.item_id, user_id),
        )
        owner_name = self._user_name(item.owner_id)
        messages = {
            DecisionCode.ALLOW_OWNER: f"ALLOW - {user_name} owns this item",
            DecisionCode.ALLOW_SHARED: f"ALLOW - item is shared with {user_name}",
            DecisionCode.WARN_NOT_OWNER: f"WARNING - {user_name} is not owner; owner is {owner_name}",
        }
        return self._finish(
            Decision(
                session_id=session_id,
                action=Action.TAKE_OUT,
                code=code,
                message=messages[code],
                user_id=user_id,
                item_id=item.item_id,
                **self._identity_fields(identity),
                item_confidence=match.confidence,
            )
        )

    def _finish(self, decision: Decision) -> Decision:
        self.repository.record_event(
            InteractionEvent(
                event_id=str(uuid4()),
                session_id=decision.session_id,
                action=decision.action,
                decision=decision.code,
                occurred_at=decision.decided_at,
                user_id=decision.user_id,
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
        self.feedback.publish(decision)
        return decision

    @staticmethod
    def _identity_fields(identity: IdentityResult) -> dict[str, object]:
        return {
            "identity_confidence": identity.confidence,
            "identity_status": identity.status,
            "identity_second_score": identity.second_score,
            "identity_margin": identity.margin,
            "identity_valid_frames": identity.valid_frames,
            "identity_vote_ratio": identity.vote_ratio,
        }

    def _user_name(self, user_id: str) -> str:
        return next(
            (user.display_name for user in self.repository.list_users() if user.user_id == user_id),
            user_id[:8],
        )
