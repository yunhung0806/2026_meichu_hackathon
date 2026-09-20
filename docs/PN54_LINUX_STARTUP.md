# PN54 Ubuntu startup audit

This is a repository-level compatibility checklist, not evidence of PN54
runtime validation. It was statically reviewed on Windows against the known
PN54 target (Ubuntu 24.04.4, kernel 7.0.0-28-generic, Ryzen AI 7 350, Radeon
860M). Do not change the PN54 kernel, drivers, ROCm, NPU runtime, system Python,
or global PyTorch for this smoke test.

## 1. Prepare an isolated environment

From the repository root, verify Python 3.10–3.12 and an existing compatible
PyTorch before installing project packages. The project `uv` environment is
isolated; it does not install PyTorch itself.

```bash
python3 --version
python3 -c 'import torch; print(torch.__version__); print(torch.cuda.is_available())'
uv sync
```

If `import torch` fails, stop. Record the PN54 Python and platform details and
choose a pinned isolated CPU build in a separate deployment task; do not run an
unqualified `pip install torch` and do not alter system packages.

## 2. Obtain and verify model artifacts

Download the small YuNet and SFace files from their recorded official sources:

```bash
uv run python scripts/download_models.py
```

Copy the Item Vision caches and the CLIP v2 TorchScript from the recorded
training/validation output into a git-ignored writable deployment directory.
Verify every SHA-256 against `services/item_vision/server.py`,
`services/item_vision/clip_v2.metadata.json`, and
`services/item_vision/validation_mi300x.json`. Do not commit model binaries.

The recorded CLIP v2 artifact is CUDA-traced and is not yet proven loadable on
PN54 CPU. A separately exported, checksum-recorded CPU-compatible TorchScript
is the remaining blocker. Sidecar startup must fail explicitly if it cannot
load; do not silently use HSV.

## 3. Start the loopback Item Vision sidecar

Use paths in a writable local deployment directory and keep the listener on
loopback. This command assumes a compatible CPU artifact and model cache are
already present:

```bash
uv run python -m services.item_vision.server \
  --host 127.0.0.1 --port 8765 --device cpu \
  --cache-dir /absolute/writable/fridge-guardian-model-cache \
  --category-model /absolute/writable/models/clip-v2-cpu.torchscript.pt \
  --category-metadata services/item_vision/clip_v2.metadata.json
```

## 4. Verify the sidecar

```bash
curl --fail http://127.0.0.1:8765/health
```

Confirm `protocol_version` is `item-vision-v1`, the requested runtime device is
CPU, and all model/checksum metadata are present before continuing.

## 5. Start the station API

Use writable absolute paths for runtime data. The database parent directory is
created by the repository, but the process user must have write permission.

```bash
export FRIDGE_DB_PATH="$HOME/.local/share/fridge-guardian/fridge_guardian.db"
export FRIDGE_CAMERA_INDEX="0"
export FRIDGE_ITEM_VISION_URL="http://127.0.0.1:8765"
export FRIDGE_ITEM_VISION_TIMEOUT="60"
export FRIDGE_API_HOST="127.0.0.1"
export FRIDGE_API_PORT="8000"
uv run fridge-guardian-api
```

The camera adapter uses the Windows DirectShow backend only on Windows and
OpenCV's portable default backend on Linux. If the camera cannot open, verify
`/dev/video*`, user permissions, the selected camera index, and that no other
process owns the camera. A missing sidecar produces an explicit 503 instead of
an HSV fallback.

## 6. Build and start the frontend

```bash
cd frontend
npm ci
npm run build
NEXT_PUBLIC_FRIDGE_API_BASE_URL=http://127.0.0.1:8000 npm run start
```

## 7. Verify local URLs

Open `http://127.0.0.1:3000`. The frontend talks only to
`http://127.0.0.1:8000`; the station API talks only to the loopback sidecar at
`http://127.0.0.1:8765`. If Lemonade is used, it remains an optional separate
loopback service at its configured URL. Do not expose these listeners to the
LAN during the smoke test.

Before claiming PN54 support, run the complete camera flow three consecutive
times and record actual sidecar/station device, median and p95 latency, and
whether the browser warning audio is audible. No Linux, PN54, CPU inference,
iGPU, or NPU performance claim was established by this static audit.
