# Remote item three-stage POC (isolated experiment)

This directory is a test bridge only. It is not imported by the Fridge
Guardian MVP and does not touch face recognition, ownership, SQLite, PUT_IN,
TAKE_OUT, PN54, or the local HSV recognizer.

```text
Windows camera + green item ROI
        |  item-only JPEG and item embeddings
        |  SSH local port forward
        v
MLSteam / MI300X: Grounding DINO -> CLIP -> DINOv2
        |
        v
Windows display + local crop/template storage
```

The full camera frame and all face data stay on Windows. The server binds only
to `127.0.0.1`; do not expose port 8765 publicly. The server is stateless and
does not save request images. Enrollment crops and item embeddings are stored
only in the git-ignored Windows `data/` directory.

## Models and downloads

- Grounding DINO-T/Swin-T, pinned revision
  `a2bb814dd30d776dcf7e30523b00659f4f141c71`, Apache-2.0, 689,359,096 bytes.
- Official OpenAI CLIP ViT-B/32 code pinned at
  `d05afc436d78f1c48dc0dbf8e5980a9d471f35f6`; repository license MIT,
  checkpoint about 338 MB. The official repository does not separately state
  a weight-only license.
- DINOv2 ViT-S/14, pinned revision
  `ed25f3a31f01632728cabb09d1542f84ab7b0056`, Apache-2.0, 88,238,968 bytes.

The server verifies all three checksums before accepting requests. First start
downloads approximately 1.12 GB. It reuses `/opt/venv`'s PyTorch/ROCm through a
separate `--system-site-packages` venv; the requirements file intentionally
does not contain torch or torchvision.

Grounding DINO and DINOv2 run in FP32 for this compatibility POC. The pinned
Transformers models produced mixed FP32/FP16 intermediate tensors with the
verified ROCm 7.1 runtime, so FP16 was rejected after a real MI300X inference
attempt. FP32 uses more accelerator memory but is the lower-risk choice on the
available MI300X and does not change the model architecture or weights.

## MLSteam server

Copy this directory to the same repository path on MLSteam, then in the
existing MLSteam terminal run:

```bash
cd /mlsteam/workspace/2026_meichu_hackathon/experiments/remote_item_three_stage_poc
/opt/venv/bin/python -m venv --system-site-packages .venv
source .venv/bin/activate
python -c "import torch, torchvision; print(torch.__version__, torch.version.hip, torchvision.__version__, torch.cuda.get_device_name(0))"
python -m pip install -r requirements-mlsteam.txt
python -m pip install --no-deps "git+https://github.com/openai/CLIP.git@d05afc436d78f1c48dc0dbf8e5980a9d471f35f6"
python server.py
```

If the validation import reports that torchvision is missing or incompatible,
stop instead of installing it. Do not run `pip install torch`, install
torchvision, upgrade either package, or install into `/opt/venv`.
Before the package install and model download, verify the active Lab, free disk,
and existing packages. The service prints its MI300X device before listening.

## Windows tunnel and camera client

Keep the server terminal running. In a second Windows PowerShell:

```powershell
ssh -N -L 8765:127.0.0.1:8765 mlsteam-amd
```

In a third PowerShell from the repository root, using the already-created
local item POC environment:

```powershell
experiments\item_three_stage_poc\.venv\Scripts\python.exe experiments\remote_item_three_stage_poc\client.py
```

- `R`: send one green-ROI event for recognition.
- `E`: run one event and save one local crop/template under an entered item ID.
- `Q`: quit.

Every response includes localization score/box, category Top 3, crop and
whole-ROI instance first/second scores and margin, per-layer MI300X latency,
total remote latency, and Windows network round-trip latency. Thresholds in
`config.json` are experimental and must be calibrated; low confidence returns
an explicit unknown state.

## Tests

```powershell
experiments\item_three_stage_poc\.venv\Scripts\python.exe -m unittest discover -s experiments\remote_item_three_stage_poc\tests -v
```

These are local protocol, scoring, privacy, crop, and storage tests. They do
not prove an MLSteam model run or a real camera event.
