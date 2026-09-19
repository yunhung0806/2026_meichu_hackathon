from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from uuid import uuid4

from fridge_guardian.adapters.camera import CameraError, OpenCVCamera
from fridge_guardian.adapters.face import SFaceIdentityProvider
from fridge_guardian.adapters.feedback import OpenCVFeedback
from fridge_guardian.adapters.item import SpatialHistogramItemRecognizer
from fridge_guardian.adapters.manual_action import ManualActionSource
from fridge_guardian.adapters.sqlite_repository import SQLiteRepository
from fridge_guardian.application import EnrollmentError, SessionCoordinator
from fridge_guardian.domain import FrameSample, utc_now


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fridge Guardian Windows local camera MVP")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "fridge_guardian.db")
    parser.add_argument(
        "--yunet", type=Path, default=PROJECT_ROOT / "models" / "face_detection_yunet_2023mar.onnx"
    )
    parser.add_argument(
        "--sface", type=Path, default=PROJECT_ROOT / "models" / "face_recognition_sface_2021dec.onnx"
    )
    parser.add_argument("--capture-seconds", type=float, default=1.8)
    parser.add_argument("--samples", type=int, default=9)
    parser.add_argument("--prepare-seconds", type=float, default=1.0)
    parser.add_argument("--item-threshold", type=float, default=0.70)
    return parser.parse_args(argv)


def capture_session(
    camera: OpenCVCamera,
    feedback: OpenCVFeedback,
    identity: SFaceIdentityProvider,
    seconds: float,
    samples: int,
    prepare_seconds: float,
):
    import cv2

    session_id = str(uuid4())
    frames: list[FrameSample] = []
    prepare_end = time.monotonic() + max(0.0, prepare_seconds)
    while time.monotonic() < prepare_end:
        frame = camera.read()
        remaining = max(0.0, prepare_end - time.monotonic())
        preview = feedback.draw(
            frame.copy(),
            f"Get ready... capture starts in {remaining:0.1f}s",
            identity.detect_for_preview(frame),
        )
        cv2.imshow("Fridge Guardian - Local MVP", preview)
        cv2.waitKey(1)
    interval = max(0.05, seconds / max(samples, 1))
    next_sample = time.monotonic()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        frame = camera.read()
        now = time.monotonic()
        if now >= next_sample and len(frames) < samples:
            frames.append(FrameSample(session_id, utc_now(), frame.copy()))
            next_sample = now + interval
        remaining = max(0.0, end - now)
        preview = feedback.draw(
            frame.copy(),
            f"Capturing... keep FACE READY {remaining:0.1f}s",
            identity.detect_for_preview(frame),
        )
        cv2.imshow("Fridge Guardian - Local MVP", preview)
        cv2.waitKey(1)
    return frames


def run(args: argparse.Namespace) -> int:
    import cv2

    feedback = OpenCVFeedback()
    action_source = ManualActionSource()
    repository = SQLiteRepository(args.db)
    camera: OpenCVCamera | None = None
    try:
        identity = SFaceIdentityProvider(args.yunet, args.sface)
        items = SpatialHistogramItemRecognizer(threshold=args.item_threshold)
        coordinator = SessionCoordinator(repository, identity, items, feedback)
        camera = OpenCVCamera(args.camera_index)
        print(f"Database: {args.db.resolve()}")
        print("Controls: U enroll user, P PUT_IN, T TAKE_OUT, Q quit")
        while True:
            frame = camera.read()
            cv2.imshow(
                "Fridge Guardian - Local MVP",
                feedback.draw(frame.copy(), face_boxes=identity.detect_for_preview(frame)),
            )
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q")):
                return 0
            if key in (ord("u"), ord("U")):
                print("Enter a short display name, then return to the camera window.")
                name = input("Display name: ").strip()
                frames = capture_session(
                    camera,
                    feedback,
                    identity,
                    args.capture_seconds,
                    args.samples,
                    args.prepare_seconds,
                )
                try:
                    user = coordinator.enroll_user(name, frames)
                    print(f"Enrolled user {user.display_name} ({user.user_id})")
                    feedback.notify(f"USER ENROLLED: {user.display_name}", (60, 210, 60))
                except EnrollmentError as exc:
                    print(f"Enrollment failed: {exc}")
                    feedback.notify(f"ENROLLMENT FAILED: {exc}", (0, 180, 255))
                continue
            action = action_source.action_for_key(key)
            if action is not None:
                frames = capture_session(
                    camera,
                    feedback,
                    identity,
                    args.capture_seconds,
                    args.samples,
                    args.prepare_seconds,
                )
                decision = coordinator.process(action, frames)
                user_name = next(
                    (
                        user.display_name
                        for user in repository.list_users()
                        if user.user_id == decision.user_id
                    ),
                    "UNKNOWN",
                )
                print(
                    f"{decision.code.value} session={decision.session_id} "
                    f"user={user_name} face={decision.identity_confidence:.3f} "
                    f"item={decision.item_confidence:.3f}"
                )
    finally:
        if camera is not None:
            camera.close()
        repository.close()
        cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except (CameraError, FileNotFoundError) as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
