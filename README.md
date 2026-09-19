# Fridge Guardian — Windows Local MVP

Fridge Guardian is a privacy-first prototype for a shared refrigerator. One
real camera captures one visible person and one handheld item in the same
short session. The user manually selects `PUT_IN` or `TAKE_OUT`; local face
and item matching then creates or checks ownership and gives an on-screen
`ALLOW`, `WARNING`, or `UNKNOWN` result. A non-owner warning also produces a
local beep.

This repository currently contains the first Windows technical/interaction
MVP only. It does **not** claim the final competition path: PN54 deployment,
MI300 fine-tuning, and the final item embedding model are deliberately out of
scope for this iteration.

## Fastest setup and start (PowerShell)

Prerequisites: Windows 10/11, a USB or built-in camera, and
[uv](https://docs.astral.sh/uv/) with CPython 3.10–3.12 available.

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv sync
uv run python scripts/download_models.py
uv run fridge-guardian
```

After setup, the shortest start command is:

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"; uv run fridge-guardian
```

Use another camera with `uv run fridge-guardian --camera-index 1`. The local
database defaults to `data/fridge_guardian.db` and is git-ignored.
Face thresholds and capture durations are centralized in `config/face.json`.
Use `--debug-face` only while calibrating to show detailed scores.

## Controls and 2–3 minute demo

Keep exactly one clear face visible and mostly front-facing. Only the item goes
inside the green `HANDHELD ITEM` box; the face does not. During enrollment,
follow the on-screen sequence: look straight, turn slightly left, then slightly
right. Do not make a full profile turn.

| Key | Operation |
| --- | --- |
| `U` | Enter a display name, then follow the straight / slight-left / slight-right prompts. The app samples 24 frames and keeps up to 8 diverse, clear templates. Repeat for a second user. |
| `P` | `PUT_IN`: identify the user; if the item is new, capture several item features, create an `item_id`, and bind it to that user. |
| `T` | `TAKE_OUT`: identify both, look up ownership, then show an ownership result or the distinct `NO_FACE`, `UNKNOWN_USER`, `AMBIGUOUS_USER`, or `UNKNOWN_ITEM` state. |
| `Q` | Quit cleanly. |

Shortest manual acceptance sequence:

1. Start the app and press `U` to enroll user A. Wait until the result returns.
2. Have user B replace A, wait a moment, then press `U` and enroll B.
3. User A holds item 1 in the green box and presses `P`.
4. User A holds the same item similarly and presses `T`: expect
   `ALLOW_OWNER`.
5. User B holds A's item and presses `T`: expect `WARN_NOT_OWNER` plus a
   two-tone local beep.
6. Repeat with two more visually distinct items. Show a non-enrolled item and
   expect `UNKNOWN_ITEM`. Run the core flow three consecutive times on the
   intended demo machine before relying on it.

Delete `data/fridge_guardian.db` only when you intentionally want to erase all
local enrollments and event history.

## Architecture and data flow

```text
OpenCV camera (one short capture, one session_id)
                   |
          SessionCoordinator
          /                \
YuNet + SFace           ROI spatial HSV
IdentityProvider       ItemRecognizer
          \                /
        ownership policy + SQLite
                   |
      OpenCV result overlay + local beep
```

- `domain`: `User`, `Item`, `InteractionEvent`, `Action`, `Decision`, and
  typed recognition results.
- `contracts`: replaceable `ActionSource`, `IdentityProvider`,
  `ItemRecognizer`, `Repository`, and `Feedback` protocols.
- `application`: same-session coordination, enrollment, `PUT_IN`, `TAKE_OUT`,
  and ownership policy. Model adapters never call each other.
- `adapters`: OpenCV camera/UI, YuNet + SFace, baseline item matching, SQLite,
  keyboard input, and Windows beep.

`ManualActionSource` is the only action source in this MVP. A future
`VisionActionSource` can implement the same contract without changing the
ownership rules.

## Local data and privacy

The camera stream and raw enrollment images stay in memory and are not saved.
Each operation creates one `session_id`; the same short frame list is passed
independently to face and item adapters. SQLite saves only:

- `users` and numeric `face_templates`;
- `items`, numeric `item_templates`, and ownership;
- `item_shares` (schema support for `ALLOW_SHARED`; no management UI yet);
- `interaction_events` with identifiers, decision, confidence, and time.

Nothing uploads face images or embeddings. The database is local personal
data: do not publish or commit it. There is no liveness detection, so this MVP
must not be used for security, access control, or consequential identity
verification.

## Face enrollment, decisions, and calibration

YuNet and SFace remain unchanged. Enrollment now rejects missing/multiple,
small, and blurry faces, removes embedding outliers, and avoids near-duplicate
templates. Raw face images are never saved. Recognition collects 16 candidates
over 1.6 seconds, requires at least 5 quality-valid frames, scores each user
against multiple templates, then uses a median across frames plus a vote check.

The identity result is explicit:

- `NO_FACE`: fewer than the configured number of quality-valid frames.
- `UNKNOWN_USER`: a face was captured but the best score is below the absolute threshold.
- `AMBIGUOUS_USER`: the best score passes, but the first/second margin or frame vote is insufficient.
- `MATCHED`: absolute score, margin, and vote checks all pass; ownership processing continues.

Default starting values are `absolute_threshold=0.55`,
`margin_threshold=0.12`, `minimum_valid_frames=5`, and
`minimum_vote_ratio=0.60`. These are conservative starting points, not universal
best thresholds. Adjust only from local A/B/unknown measurements in
`config/face.json`; increasing the score or margin threshold reduces false
acceptance but can increase rejection.

For isolated acceptance testing, use a separate database so existing data is
not modified:

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv run fridge-guardian --db data/face_stability_test.db
uv run python scripts/evaluate_faces.py --db data/face_stability_test.db --true-identity A --attempts 10 --output data/face_eval_A.csv
uv run python scripts/evaluate_faces.py --db data/face_stability_test.db --true-identity B --attempts 10 --output data/face_eval_B.csv
uv run python scripts/evaluate_faces.py --db data/face_stability_test.db --true-identity UNKNOWN --attempts 10 --output data/face_eval_unknown.csv
```

For several no-face trials, run the same evaluator with
`--true-identity NO_FACE`. The CSV stores only labels, decisions, scores,
margins, valid-frame counts, and vote ratios; it stores no images or video.

## Models and dependencies

`scripts/download_models.py` downloads from OpenCV Zoo and rejects a file
whose SHA-256 does not match. ONNX binaries are git-ignored.

| Model | Version/file | SHA-256 | Directory license |
| --- | --- | --- | --- |
| YuNet face detector | `face_detection_yunet_2023mar.onnx` | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` | MIT |
| SFace face recognizer | `face_recognition_sface_2021dec.onnx` | `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` | Apache-2.0 |

Official sources and the licensing caveat are recorded in
[`models/README.md`](models/README.md). In particular, the exact SFace
pretrained weight's training-data provenance needs a dedicated review before
public/commercial deployment.

Runtime dependencies are only NumPy and the maintained `opencv-python`
package. This MVP does not modify ROCm, PyTorch, drivers, kernels, or another
system AI runtime.

## Tests

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv run python -m unittest discover -s tests -v
```

The tests cover ownership policy, mocked `PUT_IN`/`TAKE_OUT`, explicit face
states, face-quality filtering, embedding outlier removal, diverse-template
selection, multi-frame score aggregation, unknown item, same-session
enforcement, and SQLite close/reopen persistence. They do not prove real-person
recognition quality.

## Verification status (2026-09-19)

| Area | Status | Evidence / required follow-up |
| --- | --- | --- |
| Automated logic and persistence | 20/20 local tests passed on Windows CPython 3.12.13 | Includes face quality/outlier/aggregation/status tests, mocked full flow, SQLite migration/reopen, synthetic item matching, and beep dispatch; no camera claim. |
| SQLite schema and persistence | Passed on Windows CPython 3.12.13 | Reopen test covers required records. |
| Real camera preview | Manually verified on the current Windows host | Live preview, handheld-item ROI, capture countdown, and `FACE READY` guidance were observed; camera model was not recorded. |
| Original YuNet + SFace baseline | Previously manually verified with two consenting users | The old single-decision flow had intermittent missed detections and one operational identity mismatch. |
| Enhanced multi-template / multi-frame face flow | Automated logic and ONNX-load tests only | Camera indices 0 and 1 were unavailable to the work environment, so A/B/unknown/no-face acceptance must be rerun locally after enrollment into a fresh test database. This is not an authentication system. |
| Item recognition | One real item manually verified with `--item-threshold 0.60` | `PUT_IN`, owner `TAKE_OUT`, and non-owner `TAKE_OUT` completed. Accuracy is intentionally provisional; three distinct items and three consecutive runs remain unverified. |
| Warning audio | Manually verified | The user observed `WARN_NOT_OWNER` and the local warning sound on the current Windows host. |
| PN54 / MI300 | Not connected by explicit scope | No PN54, Manta, training, or fine-tuning claim in this iteration. |

## Known limitations and troubleshooting

- Item matching is a simple spatial HSV histogram, not a learned instance
  embedding. Similar-looking packages, background changes, glare, rotation,
  occlusion, and an item not filling the ROI may become `UNKNOWN` or match
  incorrectly. Use visually distinct items for this validation round.
- Face thresholds are centralized in `config/face.json`; item matching retains
  its existing `0.70` default. Neither is a measured production value.
- The first hardware walkthrough used `--item-threshold 0.60` to exercise the
  end-to-end flow. That lower override weakens unknown-item protection and is
  evidence of integration only, not acceptable recognition accuracy.
- Face enrollment accepts frames only when YuNet sees exactly one sufficiently
  large, sharp face. Move closer, improve front lighting, remove other faces,
  and press `U` again if fewer than five diverse templates survive filtering.
- `UNKNOWN` intentionally blocks a confident ownership decision; the system
  must not guess at low confidence.
- If the camera cannot open, allow desktop-app camera access in Windows
  Settings, close other camera applications, or try `--camera-index 1`.
- If `uv` cannot create its normal cache on this machine, keep
  `UV_CACHE_DIR` set to the repository-local `.uv-cache` as shown above.
- Shared access is represented by `item_shares` and fully handled by policy,
  but this MVP intentionally has no sharing-management interface.
