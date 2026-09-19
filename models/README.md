# Local face models

Run `uv run python scripts/download_models.py` from the repository root. The
ONNX binaries are deliberately git-ignored and verified before use.

| File | Official source | SHA-256 | Stated model-directory license |
| --- | --- | --- | --- |
| `face_detection_yunet_2023mar.onnx` | OpenCV Zoo, YuNet 2023mar | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` | MIT |
| `face_recognition_sface_2021dec.onnx` | OpenCV Zoo, SFace 2021dec | `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` | Apache-2.0 |

Sources:

- https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
- https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface

The SFace model card does not fully establish the training-data provenance of
this exact pretrained weight. Treat this as a technical-demo dependency and
complete a model/data license review before public or commercial deployment.
