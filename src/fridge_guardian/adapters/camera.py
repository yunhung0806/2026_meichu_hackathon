from __future__ import annotations

import os


class CameraError(RuntimeError):
    pass


class OpenCVCamera:
    def __init__(self, camera_index: int = 0, width: int = 1280, height: int = 720) -> None:
        import cv2

        backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
        self._cv2 = cv2
        self.capture = cv2.VideoCapture(camera_index, backend)
        if not self.capture.isOpened() and backend != cv2.CAP_ANY:
            self.capture.release()
            self.capture = cv2.VideoCapture(camera_index)
        if not self.capture.isOpened():
            raise CameraError(
                f"Cannot open camera index {camera_index}. Check Windows camera permission and --camera-index."
            )
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    def read(self):
        ok, frame = self.capture.read()
        if not ok or frame is None:
            raise CameraError("Camera stopped returning frames")
        return frame

    def close(self) -> None:
        self.capture.release()
