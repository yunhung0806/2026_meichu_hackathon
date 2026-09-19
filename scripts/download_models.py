from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODELS = (
    {
        "filename": "face_detection_yunet_2023mar.onnx",
        "url": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    },
    {
        "filename": "face_recognition_sface_2021dec.onnx",
        "url": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    },
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(model: dict[str, str], destination: Path) -> None:
    target = destination / model["filename"]
    if target.exists() and sha256(target) == model["sha256"]:
        print(f"Verified existing {target.name}")
        return
    partial = target.with_suffix(target.suffix + ".part")
    print(f"Downloading {target.name} from OpenCV Zoo...")
    try:
        request = urllib.request.Request(model["url"], headers={"User-Agent": "fridge-guardian/0.1"})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        actual = sha256(partial)
        if actual != model["sha256"]:
            raise RuntimeError(
                f"Checksum mismatch for {target.name}: expected {model['sha256']}, got {actual}"
            )
        os.replace(partial, target)
        print(f"Verified {target.name}: {actual}")
    finally:
        if partial.exists():
            partial.unlink()


def main() -> int:
    destination = ROOT / "models"
    destination.mkdir(parents=True, exist_ok=True)
    try:
        for model in MODELS:
            download(model, destination)
    except Exception as exc:
        print(f"Model download failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
