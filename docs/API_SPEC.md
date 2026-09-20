# Fridge Guardian Station API

**Version:** 1.2.0

**Status:** Implemented local station bridge

**Base URL:** `http://127.0.0.1:8000/api/v1`

This document describes only the HTTP routes implemented by
`fridge_guardian.station_api`. It does not claim that the earlier proposed
cloud, PIN, upload, history, reminder, or LLM generation APIs exist.

## Runtime and security boundary

- The API is intended for the PN54 or a local development machine and binds to
  loopback by default.
- One Python process owns `OpenCVCamera`, `SQLiteRepository`, the face and item
  models, `SessionCoordinator`, and `FridgeService` for their whole lifespan.
- Requests are serialized with one application lock. Camera frames remain in
  memory and are never accepted from or returned to the browser.
- A successful identification returns the existing five-minute
  `FridgeService` bearer token. Tokens are memory-only and disappear on restart.
- CORS defaults to explicit local origins on ports 3000 and 5173. Override them
  with a comma-separated `FRIDGE_FRONTEND_ORIGINS`; wildcard and non-loopback
  origins are rejected.
- This local recognition session is not liveness-protected authentication.

All success responses use `{"success": true, "data": ...}`. Errors use:

```json
{
  "success": false,
  "error": {"code": "UNAUTHORIZED", "message": "Identify your face again"}
}
```

## Endpoint index

| Method | Route | Bearer token | Camera | Purpose |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/health` | No | No | Check that the local API initialized |
| GET | `/api/v1/station/preview` | No | Yes | Return one uncached local JPEG with the item ROI overlay |
| POST | `/api/v1/station/identify` | No | Yes | Capture locally, identify one enrolled user, and issue a token |
| POST | `/api/v1/station/enroll` | No | Yes | Capture several face poses, create a local user, and issue a token |
| POST | `/api/v1/station/inspect` | Yes | Yes | Recheck the same user and inspect one item without changing inventory |
| POST | `/api/v1/station/operate` | Yes | No | Explicitly confirm one unexpired inspection and atomically change inventory |
| GET | `/api/v1/members` | Yes | No | List other registered users available as explicit share recipients |
| GET | `/api/v1/inventory` | Yes | No | Return every present item with viewer-specific edit/take permissions |
| PATCH | `/api/v1/inventory/{item_id}` | Yes | No | Owner-only label, expiry, and public-sharing edit |
| GET | `/api/v1/history` | Yes | No | Return the current identified user's recent local interaction events |
| POST | `/api/v1/questions` | Yes | No | Retrieve inventory-aware FoodKeeper/Markdown passages |
| POST | `/api/v1/recipes/recommend` | Yes | No | Rank local recipes using the user's inventory and near-expiry items |

## `POST /api/v1/recipes/recommend`

Header: `Authorization: Bearer <access_token>`.

```json
{"question": "請用快到期的食材推薦料理"}
```

The response contains `ingredients`, excluded unsafe/out-of-guidance items,
up to five ranked `recipes`, and an optional Lemonade `answer`. Package expiry
always takes precedence; FoodKeeper dates are labeled as general guidance and
are never presented as package expiry. If Lemonade is unavailable, retrieval
results are still returned with `RETRIEVAL_ONLY` or `LLM_UNAVAILABLE` status.
Recipe JSON is loaded from `FRIDGE_RECIPE_DIR` when set, otherwise from
`data/knowledge/recipes`.

## `GET /api/v1/health`

```json
{
  "success": true,
  "data": {"status": "ok", "mode": "local", "camera_owner": "python"}
}
```

If real startup cannot open the database, models, or camera, the server does not
become healthy.

## `GET /api/v1/station/preview`

Returns one `image/jpeg` snapshot from the same Python-owned camera used by
identify, enroll, and inspect. The backend draws the exact configured item ROI
in green and sets `Cache-Control: no-store`. The snapshot is serialized with
other station camera operations, remains local to the loopback API, is not
persisted, and is not sent to the Item Vision sidecar or Manta.

## `POST /api/v1/station/identify`

Body: none. The backend captures a fresh short session and calls
`FridgeService.identify`.

```json
{
  "success": true,
  "data": {
    "access_token": "memory-only-random-token",
    "token_type": "bearer",
    "user_id": "opaque-user-id",
    "display_name": "Alice",
    "expires_at": "2026-09-19T08:05:00+00:00"
  }
}
```

Unknown or invalid identity returns `401 UNKNOWN_USER`. Face templates and
embeddings are never returned.

## `POST /api/v1/station/enroll`

```json
{"display_name": "Alice"}
```

The station waits for the configured preparation delay, captures the longer
enrollment sequence, and reuses `SessionCoordinator.enroll_user`. The user
should look forward and then turn slightly left and right. A successful request
stores only numeric face templates in SQLite and returns the same memory-only
login shape as identification. Insufficient clear face samples return
`422 ENROLLMENT_FAILED`; raw camera frames are never returned or persisted.

## Two-stage item operation

Scanning and database mutation are deliberately separate. `inspect` captures
one in-memory camera session, revalidates the signed-in face, sends only the
green item ROI JPEG plus opaque DINOv2 gallery templates to the loopback Item
Vision service, then retains embeddings and the candidate allowlist in memory
for 120 seconds. It does not save an image or modify inventory.

### `POST /api/v1/station/inspect`

Header: `Authorization: Bearer <access_token>`.

```json
{"action": "PUT_IN"}
```

The response includes an opaque `inspection_id`, expiry, matched identity,
localization status, category Top-3 suggestions, instance status, eligible
candidate display rows, a suggested editable label, and an authorization-filtered
inventory snapshot for every `TAKE_OUT` review. Raw embeddings never reach the browser.
`UNKNOWN_CATEGORY` is non-blocking. Localization failure returns a
non-committable inspection that requires rescan. Sidecar failure returns
`503 ITEM_VISION_UNAVAILABLE` and never falls back to HSV.

### `POST /api/v1/station/operate`

Header: `Authorization: Bearer <access_token>`.

### PUT_IN request

```json
{
  "inspection_id": "opaque-short-lived-id",
  "action": "PUT_IN",
  "confirmed": true,
  "label": "reviewed milk",
  "selected_item_id": null,
  "add_as_new": true,
  "shared": false,
  "shared_user_ids": ["opaque-recipient-user-id"],
  "expires_on": "2026-09-22"
}
```

`label` is user-reviewed, required for put-in, trimmed, and limited to 200
characters. Category suggestions and edited labels never choose `item_id`.
For `MATCHED`, `AMBIGUOUS`, and `NO_MATCH`, the user may choose an eligible
offered item or explicitly set `add_as_new`. The browser defaults every PUT_IN
to `add_as_new` because visually identical products may be separate physical
units; an eligible historical item can still be selected explicitly. If no
eligible candidate remains after filtering, adding a new item remains
available. When
`add_as_new` is true, `selected_item_id` must be null and the backend creates
the opaque ID. DINOv2 crop and ROI templates are saved under that final item
ID. `expires_on` may be `null`; `shared` defaults to `false`. The browser uses
`shared_user_ids` for selected recipients. The backend rejects unknown IDs,
the owner, or a request that combines selected recipients with legacy
all-member `shared=true` access.

### TAKE_OUT request

```json
{
  "inspection_id": "opaque-short-lived-id",
  "action": "TAKE_OUT",
  "confirmed": true,
  "selected_item_id": "offered-opaque-item-id"
}
```

`MATCHED` may preselect the best authorized AI candidate, but every status may
be corrected using only the authorized inventory snapshot returned by that
inspection. `AMBIGUOUS` and `NO_MATCH` require an explicit choice. Typed labels
never identify a take-out item. If the snapshot is empty, inspection returns a
non-committable `NO_AUTHORIZED_ITEMS` review state and no inventory mutation is
possible.

If the top-ranked `MATCHED` or `AMBIGUOUS` result points to another user's
present private item, the unauthorized row remains filtered out and the inspection returns
`review_state` and `review_decision` as `WARN_NOT_OWNER`. Local feedback plays
the warning tone immediately. The item remains present; the user may only
correct the AI result by choosing a different item from the authorization-
filtered snapshot. If that snapshot is empty, confirmation stays disabled.

The commit rejects missing confirmation, unknown/expired/used inspections,
action or identity mismatch, arbitrary IDs, stale inventory, and unauthorized
choices. The selected row is rechecked for presence and authorization at commit
time. A successful inspection is consumed once. Failed validation does not
pretend that inventory changed.

## `GET /api/v1/history`

Header: `Authorization: Bearer <access_token>`. Returns at most 50 relevant
local interaction events for the signed-in user, newest first. Normal events
remain private to the acting user. A blocked `WARN_NOT_OWNER` take-out is
recorded for both the actor and the item owner so each sees the same denied
physical interaction; it does not mutate inventory.

### Operation response

```json
{
  "success": true,
  "data": {
    "outcome": "ALLOW",
    "decision": "ALLOW_OWNER",
    "message": "Take-out recorded",
    "session_id": "opaque-session-id",
    "user_id": "opaque-user-id",
    "item_id": "opaque-item-id",
    "identity_confidence": 0.91,
    "item_confidence": 0.84,
    "warnings": [],
    "decided_at": "2026-09-19T08:10:00+00:00"
  }
}
```

`outcome` is the frontend summary:

- `ALLOW`: `ITEM_REGISTERED`, `ALLOW_OWNER`, or `ALLOW_SHARED`.
- `WARNING`: non-owner denial or an already-present item.
- `UNKNOWN`: no confident user/item decision. An unknown take-out does not
  mutate inventory.

The detailed `decision` remains the existing domain decision string. A changed
person returns `403 PERSON_CHANGED`; missing, invalid, or expired tokens return
`401 UNAUTHORIZED`; invalid input returns `422 VALIDATION_ERROR`.

## `GET /api/v1/inventory`

Header: `Authorization: Bearer <access_token>`.

```json
{
  "success": true,
  "data": {
    "items": [
      {
        "item_id": "opaque-item-id",
        "label": "milk",
        "owner_id": "opaque-user-id",
        "owner_display_name": "Enoch",
        "owner_name": "Enoch",
        "shared": false,
        "access_type": "SHARED_DIRECT",
        "can_edit": false,
        "can_take": true,
        "shared_user_ids": [],
        "shared_user_names": [],
        "put_at": "2026-09-19T08:08:00+00:00",
        "expires_on": "2026-09-22"
      }
    ]
  }
}
```

Every authenticated fridge user sees every present row. `can_edit` is true only
for the owner. `can_take` is true for the owner, an all-user shared row, or an
existing direct share. A private row is therefore visible to another user but
is neither editable nor selectable for take-out. The browser groups only by
trimmed, case-insensitive label for display; every opaque `item_id`, expiry,
owner, sharing state, and put-in time remains independent in SQLite. Biometric
templates, images, embeddings, tokens, and removed rows are never returned.

## `PATCH /api/v1/inventory/{item_id}`

Header: `Authorization: Bearer <access_token>`.

The owner may send any non-empty subset of:

```json
{
  "label": "麥香紅茶",
  "expires_on": "2026-09-25",
  "shared": false,
  "shared_user_ids": ["opaque-user-id"]
}
```

`label` is trimmed and must contain 1–200 characters. `expires_on` is an ISO
date or `null`. `shared` is a JSON boolean retained for all-user compatibility;
`shared_user_ids` selects specific registered users and cannot be combined with
`shared=true`. Unknown recipients are rejected. Unknown fields, `item_id`, owner,
timestamps, presence, and embeddings are rejected. Unknown rows return 404,
valid non-owners return 403, and absent rows return 409. Setting `shared=false`
with an empty recipient list revokes direct grants so the item becomes
owner-only. The response includes owner-visible recipient IDs/names using the
same public inventory representation. Every successful update creates an
`INVENTORY_EDIT` / `ITEM_UPDATED` local history event.

The management inventory is global, but recipe and FoodKeeper/RAG retrieval
continue to use only food the current user owns or may take through sharing.

## `POST /api/v1/questions`

Header: `Authorization: Bearer <access_token>`.

```json
{
  "question": "我週末要回家，哪些食物需要先處理？",
  "category": "storage"
}
```

`question` must contain 1-2000 characters. `category` is `storage` by default
and may also be `recipes`. Retrieval authenticates first, reads only the
current user's present inventory, and returns FoodKeeper and matching local
Markdown passages. Package expiry dates take priority over general FoodKeeper
guidance and are included as inventory passages. A question that directly names
a known FoodKeeper food can retrieve its generic guidance even when that food
is not currently in inventory. Broad inventory questions still require
matching inventory data.

```json
{
  "success": true,
  "data": {
    "status": "OK",
    "answer": "蘋果仍在一般冷藏建議區間內。",
    "sources": [
      {
        "source": "foodkeeper/蘋果",
        "text": "apple：USDA FoodKeeper 的一般冷藏保存指引為 ..."
      }
    ]
  }
}
```

The RAG transport and sources are real. When `FRIDGE_LEMONADE_MODEL` is set,
the backend sends those passages to the loopback Lemonade chat-completions API.
Without that setting it reports `LLM_NOT_CONFIGURED`; an unavailable or invalid
Lemonade response reports `LLM_UNAVAILABLE` while preserving the sources.

## Local configuration

| Variable | Default |
| --- | --- |
| `FRIDGE_API_HOST` | `127.0.0.1` |
| `FRIDGE_API_PORT` | `8000` |
| `FRIDGE_FRONTEND_ORIGINS` | explicit localhost and 127.0.0.1 origins on 3000 and 5173 |
| `FRIDGE_CAMERA_INDEX` | `0` |
| `FRIDGE_DB_PATH` | `data/fridge_guardian.db` |
| `FRIDGE_FACE_CONFIG` | `config/face.json` |
| `FRIDGE_YUNET_PATH` | bundled model path under `models/` |
| `FRIDGE_SFACE_PATH` | bundled model path under `models/` |
| `FRIDGE_ITEM_VISION_URL` | `http://127.0.0.1:8765` (loopback only) |
| `FRIDGE_ITEM_VISION_TIMEOUT` | `60` seconds |
| `FRIDGE_WARNING_AUDIO_PATH` | unset; local `.mp3`/`.wav`, kept outside Git |
| `FRIDGE_WARNING_AUDIO_MODE` | `browser` when a file is configured, otherwise `system` |
| `FRIDGE_RECIPE_DIR` | bundled `data/knowledge/recipes` |
| `FRIDGE_LEMONADE_MODEL` | unset (retrieval only) |
| `FRIDGE_LEMONADE_BASE_URL` | `http://127.0.0.1:13305/v1` |
| `FRIDGE_LEMONADE_TIMEOUT` | `60` seconds |

The frontend reads `NEXT_PUBLIC_FRIDGE_API_BASE_URL`, defaulting to
`http://127.0.0.1:8000`.

## Explicitly not implemented

There are no HTTP routes here for history, PIN confirmation, image upload,
Cloudflare D1, reminders, or notification delivery. The frontend labels
history as not connected. LLM generation is opt-in and requires an already
installed/running Lemonade model on the same machine.
