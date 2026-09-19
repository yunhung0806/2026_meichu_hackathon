# Item Vision sidecar (`item-vision-v1`)

This loopback-only service owns exactly one pipeline: item ROI JPEG to
Grounding DINO localization, CLIP v2 semantic Top-3 suggestions, DINOv2
384-dimensional normalized embeddings, and gallery ranking. It receives no
face, user, owner, action, expiry, sharing, token, or authorization data and
does not persist requests, images, galleries, or embeddings.

The model binaries are deliberately not stored in Git. Expected revisions and
SHA-256 values are in `server.py`, `clip_v2.metadata.json`, and
`validation_mi300x.json`. Grounding DINO Tiny and DINOv2 Small are retrieved
from their pinned Hugging Face revisions. The fine-tuned CLIP v2 artifact must
be copied from the recorded training output and must match the metadata
checksum.

MI300X example (uses the platform PyTorch; do not install or upgrade torch):

```bash
PYTHONPATH=. /opt/venv/bin/python -m services.item_vision.server \
  --device cuda:0 --port 8765 \
  --cache-dir /mlsteam/workspace/fridge-guardian-model-cache \
  --category-model /mlsteam/workspace/fridge-guardian-runs/clip-v2/export/clip_vit_b32_item_classifier.clip-v2-mi300x.torchscript.pt \
  --category-metadata services/item_vision/clip_v2.metadata.json
```

When the sidecar runs on Manta, keep it bound to Manta loopback and create the
Windows tunnel separately:

```powershell
ssh -N -L 8765:127.0.0.1:8765 mlsteam-amd
```

Then configure the station API with
`FRIDGE_ITEM_VISION_URL=http://127.0.0.1:8765`. The API rejects non-loopback
sidecar URLs.

PN54 compatibility smoke uses `--device cpu` in an isolated environment with
an already compatible PyTorch. No CPU performance has been measured. The
recorded MI300X TorchScript is CUDA-traced and may not load on CPU; if so,
startup fails explicitly. Export/copy a checksum-recorded CPU-compatible CLIP
v2 TorchScript before retrying. Do not change the PN54 kernel, drivers, ROCm,
NPU runtime, or system Python for this smoke test.

Exact remaining PN54 smoke: in an isolated venv that already has a compatible
PyTorch/torchvision, copy the three checksum-verified artifacts/cache and this
folder, start the command above with `--device cpu`, confirm `/health` reports
`requested_runtime_device: cpu`, then send one item-only JPEG ROI through
`/infer`. Record 30 post-warm-up requests before making any latency/FPS claim.
This procedure has not yet been run on PN54.

The service exposes `GET /health` and `POST /infer`. Both successful responses
identify protocol `item-vision-v1`; `/infer` accepts only `request_id`, one
Base64 JPEG ROI, and at most 100 validated opaque gallery templates.
