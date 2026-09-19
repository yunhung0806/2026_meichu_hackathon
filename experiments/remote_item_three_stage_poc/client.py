from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from common import encode_jpeg_rgb
from storage import LocalTemplateStore


ROOT = Path(__file__).resolve().parent


def roi_pixels(frame: np.ndarray, roi: list[float]) -> tuple[int, int, int, int]:
    height, width = frame.shape[:2]
    return (
        int(width * roi[0]),
        int(height * roi[1]),
        int(width * roi[2]),
        int(height * roi[3]),
    )


def remote_infer(
    server_url: str,
    roi_bgr: np.ndarray,
    gallery: list[dict[str, object]],
    timeout_seconds: float,
) -> tuple[dict[str, Any], float]:
    rgb = Image.fromarray(roi_bgr[:, :, ::-1])
    body = json.dumps(
        {
            "roi_jpeg_base64": encode_jpeg_rgb(rgb),
            "gallery": gallery,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        server_url.rstrip("/") + "/infer",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        result = json.loads(response.read())
    return result, (time.perf_counter() - started) * 1000.0


def open_camera(index: int):
    backend = cv2.CAP_DSHOW if __import__("os").name == "nt" else cv2.CAP_ANY
    camera = cv2.VideoCapture(index, backend)
    if not camera.isOpened():
        camera.release()
        camera = cv2.VideoCapture(index)
    if not camera.isOpened():
        raise RuntimeError(f"Cannot open camera index {index}")
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    return camera


def crop_from_result(roi_bgr: np.ndarray, result: dict[str, Any]) -> np.ndarray | None:
    localization = result.get("localization", {})
    box = localization.get("crop_box")
    if localization.get("status") != "OK" or not box:
        return None
    x1, y1, x2, y2 = (int(value) for value in box)
    crop = roi_bgr[y1:y2, x1:x2].copy()
    return crop if crop.size else None


def concise_result(result: dict[str, Any], network_latency_ms: float) -> dict[str, Any]:
    clean = {key: value for key, value in result.items() if key != "embeddings"}
    clean["network_round_trip_ms"] = network_latency_ms
    return clean


def event_summary(result: dict[str, Any]) -> str:
    localization = result.get("localization", {})
    if localization.get("status") != "OK":
        return str(localization.get("status", "REMOTE_ERROR"))
    category = result.get("category", {})
    top3 = category.get("top3", [])
    category_text = top3[0]["label"] if category.get("status") == "OK" and top3 else category.get("status")
    instance = result.get("instance", {})
    item_text = instance.get("first_item_id") if instance.get("status") == "MATCHED" else instance.get("status")
    return f"{category_text} | {item_text} | remote {result.get('total_latency_ms', 0):.0f} ms"


def append_event(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def run(args: argparse.Namespace) -> None:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    store = LocalTemplateStore(args.data_dir / "items")
    camera = open_camera(args.camera_index)
    last_message = "R Remote recognize   E Enroll local template   Q Quit"
    try:
        while True:
            ok, frame = camera.read()
            if not ok or frame is None:
                raise RuntimeError("Camera stopped returning frames")
            x1, y1, x2, y2 = roi_pixels(frame, config["roi"])
            display = frame.copy()
            cv2.rectangle(display, (x1, y1), (x2, y2), (50, 230, 80), 3)
            cv2.rectangle(display, (0, 0), (display.shape[1], 78), (22, 22, 22), -1)
            cv2.putText(
                display,
                "REMOTE ITEM POC   R Recognize   E Enroll view   Q Quit",
                (18, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.70,
                (245, 245, 245),
                2,
            )
            cv2.putText(
                display,
                "Only green ROI JPEG goes through the SSH tunnel; no face/full frame",
                (18, 62),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (190, 230, 190),
                2,
            )
            cv2.putText(
                display,
                last_message[:115],
                (18, display.shape[0] - 22),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.60,
                (0, 220, 255),
                2,
            )
            cv2.imshow("Remote Item Three-Stage POC", display)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q")):
                break
            if key not in (ord("r"), ord("R"), ord("e"), ord("E")):
                continue
            roi_bgr = frame[y1:y2, x1:x2].copy()
            action = "enroll" if key in (ord("e"), ord("E")) else "recognize"
            try:
                result, network_latency = remote_infer(
                    args.server_url, roi_bgr, store.gallery(), args.timeout
                )
                record = {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "action": action,
                    **concise_result(result, network_latency),
                }
                crop = crop_from_result(roi_bgr, result)
                if action == "enroll":
                    embeddings = result.get("embeddings")
                    if crop is None or not embeddings:
                        raise ValueError("Remote localization did not produce an enrollable crop")
                    print("Enter item_id (letters/digits/_/-): ", end="", flush=True)
                    item_id = input().strip()
                    template_id = store.enroll(
                        item_id,
                        crop,
                        embeddings["crop"],
                        embeddings["roi"],
                        concise_result(result, network_latency),
                    )
                    record.update({"enrolled_item_id": item_id, "template_id": template_id})
                append_event(args.data_dir / "results" / "events.jsonl", record)
                print(json.dumps(record, ensure_ascii=False, indent=2), flush=True)
                last_message = event_summary(result)
                if crop is not None:
                    cv2.imshow("Remote final item crop", crop)
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last_message = f"REMOTE_ERROR: {exc}"
                print(last_message, flush=True)
    finally:
        camera.release()
        cv2.destroyAllWindows()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows camera client for isolated MLSteam item POC")
    parser.add_argument("--server-url", default="http://127.0.0.1:8765")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    return parser.parse_args()


def main() -> int:
    run(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
