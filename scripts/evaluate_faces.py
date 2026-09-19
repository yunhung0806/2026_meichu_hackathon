"""Interactive local face evaluation; writes metrics only, never camera images."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from fridge_guardian.adapters.camera import OpenCVCamera
from fridge_guardian.adapters.face import FaceRecognitionSettings, SFaceIdentityProvider
from fridge_guardian.adapters.feedback import OpenCVFeedback
from fridge_guardian.adapters.sqlite_repository import SQLiteRepository
from fridge_guardian.app import PROJECT_ROOT, capture_session


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--true-identity", required=True, help="Enrolled display name, UNKNOWN, or NO_FACE")
    parser.add_argument("--attempts", type=int, default=10)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "fridge_guardian.db")
    parser.add_argument("--face-config", type=Path, default=PROJECT_ROOT / "config" / "face.json")
    parser.add_argument("--yunet", type=Path, default=PROJECT_ROOT / "models" / "face_detection_yunet_2023mar.onnx")
    parser.add_argument("--sface", type=Path, default=PROJECT_ROOT / "models" / "face_recognition_sface_2021dec.onnx")
    parser.add_argument("--output", type=Path, help="Optional CSV path; contains scores only")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.attempts <= 0:
        raise SystemExit("--attempts must be positive")
    settings = FaceRecognitionSettings.load(args.face_config)
    identity = SFaceIdentityProvider(args.yunet, args.sface, settings)
    feedback = OpenCVFeedback(debug=True)
    repository = SQLiteRepository(args.db)
    camera = OpenCVCamera(args.camera_index)
    output_handle = None
    try:
        users = {user.user_id: user.display_name for user in repository.list_users()}
        valid_truth = set(users.values()) | {"UNKNOWN", "NO_FACE"}
        if args.true_identity not in valid_truth:
            choices = ", ".join(sorted(valid_truth))
            raise SystemExit(f"Unknown --true-identity. Choose one of: {choices}")
        output_handle = (
            args.output.open("w", newline="", encoding="utf-8")
            if args.output
            else sys.stdout
        )
        writer = csv.writer(output_handle)
        writer.writerow(
            [
                "attempt",
                "true_identity",
                "predicted_identity",
                "status",
                "best_score",
                "second_score",
                "margin",
                "valid_frames",
                "vote_ratio",
            ]
        )
        for attempt in range(1, args.attempts + 1):
            input(
                f"Attempt {attempt}/{args.attempts}: prepare {args.true_identity}, then press Enter...",
            )
            frames = capture_session(
                camera,
                feedback,
                identity,
                settings.recognition_window_seconds,
                settings.recognition_candidate_frames,
                settings.prepare_seconds,
                mode="recognition",
            )
            result = identity.identify(
                frames[0].session_id,
                frames,
                repository.list_face_templates(),
            )
            predicted = users.get(result.user_id, result.status.value)
            writer.writerow(
                [
                    attempt,
                    args.true_identity,
                    predicted,
                    result.status.value,
                    f"{result.confidence:.6f}",
                    f"{result.second_score:.6f}",
                    f"{result.margin:.6f}",
                    result.valid_frames,
                    f"{result.vote_ratio:.6f}",
                ]
            )
            output_handle.flush()
        return 0
    finally:
        camera.close()
        repository.close()
        if output_handle is not None and output_handle is not sys.stdout:
            output_handle.close()
        import cv2

        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
