from __future__ import annotations

import os
import time

from fridge_guardian.domain import Decision, DecisionCode


class OpenCVFeedback:
    COLORS = {
        DecisionCode.ALLOW_OWNER: (60, 210, 60),
        DecisionCode.ALLOW_SHARED: (60, 210, 60),
        DecisionCode.ITEM_REGISTERED: (60, 210, 60),
        DecisionCode.ITEM_ALREADY_REGISTERED: (60, 210, 210),
        DecisionCode.WARN_NOT_OWNER: (40, 40, 240),
        DecisionCode.UNKNOWN_USER: (0, 180, 255),
        DecisionCode.UNKNOWN_ITEM: (0, 180, 255),
    }

    def __init__(self) -> None:
        self.last_decision: Decision | None = None
        self.last_published_at = 0.0
        self.notice: str | None = None
        self.notice_color = (255, 255, 255)

    def publish(self, decision: Decision) -> None:
        self.last_decision = decision
        self.notice = None
        self.last_published_at = time.monotonic()
        if decision.code is DecisionCode.WARN_NOT_OWNER or decision.warnings:
            self._warning_beep()

    @staticmethod
    def _warning_beep() -> None:
        if os.name == "nt":
            try:
                import winsound

                winsound.Beep(880, 220)
                winsound.Beep(660, 260)
                return
            except RuntimeError:
                pass
        print("\a", end="", flush=True)

    def notify(self, message: str, color: tuple[int, int, int] = (255, 255, 255)) -> None:
        self.notice = message
        self.notice_color = color
        self.last_decision = None
        self.last_published_at = time.monotonic()

    def draw(
        self,
        frame,
        transient_message: str | None = None,
        face_boxes: list[tuple[int, int, int, int, float]] | None = None,
    ):
        import cv2

        height, width = frame.shape[:2]
        cv2.rectangle(frame, (0, 0), (width, 92), (25, 25, 25), -1)
        cv2.putText(frame, "U Enroll user   P PUT_IN   T TAKE_OUT   Q Quit", (18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (245, 245, 245), 2)
        cv2.putText(frame, "Face visible + one item filling the green box", (18, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.66, (190, 230, 190), 2)
        if face_boxes is not None:
            if len(face_boxes) == 1:
                face_status = "FACE READY"
                face_color = (60, 220, 60)
            elif len(face_boxes) == 0:
                face_status = "NO FACE"
                face_color = (40, 40, 240)
            else:
                face_status = "ONE FACE ONLY"
                face_color = (0, 180, 255)
            cv2.putText(
                frame,
                face_status,
                (max(18, width - 245), 64),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.68,
                face_color,
                2,
            )
            for x, y, box_width, box_height, _score in face_boxes:
                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + box_width, y + box_height),
                    face_color,
                    2,
                )
        from fridge_guardian.adapters.item import SpatialHistogramItemRecognizer

        x1, y1, x2, y2 = SpatialHistogramItemRecognizer.roi_pixels(frame)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (50, 230, 80), 3)
        cv2.putText(frame, "HANDHELD ITEM", (x1, max(115, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (50, 230, 80), 2)
        message = transient_message
        color = (255, 255, 255)
        if message is None and time.monotonic() - self.last_published_at < 8.0:
            if self.last_decision:
                message = f"{self.last_decision.code.value}: {self.last_decision.message}"
                color = (40, 40, 240) if self.last_decision.warnings else self.COLORS[self.last_decision.code]
            elif self.notice:
                message = self.notice
                color = self.notice_color
        if message:
            cv2.rectangle(frame, (0, height - 64), (width, height), (20, 20, 20), -1)
            cv2.putText(frame, message[:100], (18, height - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
        return frame
