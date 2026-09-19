from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from uuid import uuid4

from fridge_guardian.adapters.camera import CameraError, OpenCVCamera
from fridge_guardian.adapters.face import FaceRecognitionSettings, SFaceIdentityProvider
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
    parser.add_argument(
        "--face-config", type=Path, default=PROJECT_ROOT / "config" / "face.json"
    )
    parser.add_argument("--debug-face", action="store_true")
    parser.add_argument("--item-threshold", type=float, default=0.70)
    return parser.parse_args(argv)


def capture_session(
    camera: OpenCVCamera,
    feedback: OpenCVFeedback,
    identity: SFaceIdentityProvider,
    seconds: float,
    samples: int,
    prepare_seconds: float,
    mode: str = "recognition",
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
    started = time.monotonic()
    end = started + seconds
    while time.monotonic() < end:
        frame = camera.read()
        now = time.monotonic()
        if now >= next_sample and len(frames) < samples:
            frames.append(FrameSample(session_id, utc_now(), frame.copy()))
            next_sample = now + interval
        remaining = max(0.0, end - now)
        progress = (now - started) / max(seconds, 0.01)
        if mode == "enrollment":
            if progress < 0.34:
                guidance = "ENROLL 1/3: look straight at the camera"
            elif progress < 0.67:
                guidance = "ENROLL 2/3: turn SLIGHTLY left"
            else:
                guidance = "ENROLL 3/3: turn SLIGHTLY right"
        else:
            guidance = "IDENTIFY: keep your face clear and mostly front"
        preview = feedback.draw(
            frame.copy(),
            f"{guidance} ({remaining:0.1f}s)",
            identity.detect_for_preview(frame),
        )
        cv2.imshow("Fridge Guardian - Local MVP", preview)
        cv2.waitKey(1)
    return frames


def run(args: argparse.Namespace) -> int:
    import cv2

    settings = FaceRecognitionSettings.load(args.face_config)
    feedback = OpenCVFeedback(debug=args.debug_face)
    action_source = ManualActionSource()
    repository = SQLiteRepository(args.db)
    camera: OpenCVCamera | None = None
    try:
        identity = SFaceIdentityProvider(args.yunet, args.sface, settings)
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
                    settings.enrollment_window_seconds,
                    settings.enrollment_candidate_frames,
                    settings.prepare_seconds,
                    mode="enrollment",
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
                    settings.recognition_window_seconds,
                    settings.recognition_candidate_frames,
                    settings.prepare_seconds,
                    mode="recognition",
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
                summary = (
                    f"{decision.code.value} session={decision.session_id} user={user_name}"
                )
                if args.debug_face:
                    summary += (
                        f" face={decision.identity_confidence:.3f} "
                        f"second={decision.identity_second_score:.3f} "
                        f"margin={decision.identity_margin:.3f} "
                        f"valid={decision.identity_valid_frames} "
                        f"votes={decision.identity_vote_ratio:.2f}"
                    )
                print(summary)
    finally:
        if camera is not None:
            camera.close()
        repository.close()
        cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except (CameraError, FileNotFoundError, ValueError) as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
