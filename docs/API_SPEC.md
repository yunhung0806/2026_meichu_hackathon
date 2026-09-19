# Fridge Guardian Station API

**Version:** 1.1.0

**Status:** Implemented local station bridge

**Base URL:** `http://127.0.0.1:8000/api/v1`

This document describes only the HTTP routes implemented by
`fridge_guardian.station_api`. It does not claim that the earlier proposed
cloud, PIN, upload, or reminder APIs exist.

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
| POST | `/api/v1/station/identify` | No | Yes | Capture locally, identify one enrolled user, and issue a token |
| POST | `/api/v1/station/operate` | Yes | Yes | Recheck the same user, recognize one item, and process `PUT_IN` or `TAKE_OUT` |
| GET | `/api/v1/inventory` | Yes | No | Return the current identified user's present SQLite inventory |
| POST | `/api/v1/questions` | Yes | No | Retrieve inventory-aware FoodKeeper/Markdown passages |

| GET | `/api/v1/history` | Yes | No | Latest 100 take-out decisions involving the user or their items |
| POST | `/api/v1/recipes/recommend` | Yes | No | Expiry-prioritized recipe retrieval and optional local RAG |

## `GET /api/v1/health`

```json
{
  "success": true,
  "data": {"status": "ok", "mode": "local", "camera_owner": "python"}
}
```

If real startup cannot open the database, models, or camera, the server does not
become healthy.

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

## `POST /api/v1/station/operate`

Header: `Authorization: Bearer <access_token>`.

### PUT_IN request

```json
{
  "action": "PUT_IN",
  "label": "milk",
  "shared": false,
  "expires_on": "2026-09-22"
}
```

`label` is required, trimmed, and limited to 200 characters. It is confirmed or
entered by the user; the spatial HSV instance matcher does not generate food
names. `expires_on` may be `null`. `shared` defaults to `false`.

### TAKE_OUT request

```json
{"action": "TAKE_OUT"}
```

One request captures a fresh session, rechecks the user, recognizes the item,
applies ownership policy, records the event, and updates SQLite only when the
decision allows take-out.

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
        "shared": 0,
        "put_at": "2026-09-19T08:08:00+00:00",
        "expires_on": "2026-09-22"
      }
    ]
  }
}
```

The current `FridgeService` returns present items owned by the identified user.
It does not expose biometric templates, raw images, removed records, or events.

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

## `GET /api/v1/history`

Header: `Authorization: Bearer <access_token>`.

Returns `{"success": true, "data": {"events": [...]}}`. Each event includes
`event_id`, `session_id`, `occurred_at` (ISO timestamp), `decision`, `taker_id`,
`taker_name`, `item_id`, `label`, `owner_id`, and `owner_name`. Unknown users or
items have null identifying fields. Only `TAKE_OUT` events where the signed-in
user is the actor or item owner are returned, newest first, at most 100.
Missing, invalid, or expired tokens return `401 UNAUTHORIZED`.

History reuses persisted interaction events, including earlier CLI events.
Names and labels resolve from current user/item records, not historical snapshots.
`ALLOW_OWNER` and `ALLOW_SHARED` indicate an allowed take-out decision;
`WARN_NOT_OWNER` is an attempted take-out warning, and other decisions are
unconfirmed. These are system decisions, not independent sensor confirmation
that an item physically left the refrigerator. No camera capture is performed.

## `POST /api/v1/recipes/recommend`

Header: `Authorization: Bearer <access_token>`. JSON body:
`{"question":"現在可以煮什麼？"}` (optional question; 1–2000 characters).

The success envelope's `data` contains `today`, `soon_days` (3), `ingredients`,
`excluded`, `recipes`, `answer`, and `status`. Ingredient records extend inventory
with `days_left`, `priority`, `date_basis` (`PACKAGE`, `FOODKEEPER`, `UNKNOWN`),
`reference_date`, and `status`. FoodKeeper reference dates use the lower bound
of its storage guidance; they are not expiry dates. Beyond-guidance and expired
items are excluded. Unknown dates remain explicitly marked for user checking.

Recipe records contain `id`, `title`, `ingredients` (canonical names and aliases),
`matched`, `missing`, `use_first`, `pantry`, `steps`, `source`, `source_title`, and
`provenance`. Retrieval requires at least one matching ingredient. Rank uses
urgent matched items first, missing ingredient count, matched ingredient count,
then question-title lexical overlap. At most five results are returned.
Names match normalized aliases exactly to avoid false matches such as eggplant
or 蛋糕 matching eggs. Only the current owner's present inventory is used.

Statuses: `OK` (LLM generated), `RETRIEVAL_ONLY` (no model configured),
`LLM_UNAVAILABLE` (cards available but generation failed), `EMPTY_INVENTORY`,
or `NO_MATCH`. Invalid/expired tokens return 401; invalid questions return 422.
The endpoint never captures images or mutates inventory. Optional generation
uses Ollama when FRIDGE_OLLAMA_MODEL is set; otherwise it reuses configured
Lemonade. The respective model timeout applies; station requests remain serialized during generation.

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
| `FRIDGE_ITEM_THRESHOLD` | `0.70` |
| `FRIDGE_LEMONADE_MODEL` | unset (retrieval only) |
| `FRIDGE_LEMONADE_BASE_URL` | `http://127.0.0.1:13305/v1` |
| `FRIDGE_LEMONADE_TIMEOUT` | `60` seconds |

| `FRIDGE_RECIPE_DIR` | `data/knowledge/recipes` under the project root |
| `FRIDGE_OLLAMA_MODEL` | unset: retrieval only; set to an already installed local model for RAG |

The frontend reads `NEXT_PUBLIC_FRIDGE_API_BASE_URL`, defaulting to
`http://127.0.0.1:8000`.

## Explicitly not implemented

There are no HTTP routes here for PIN confirmation, image upload,
Cloudflare D1, reminders, notification delivery, or general-purpose question answering.
Ask the Fridge supports recipe recommendations and FoodKeeper storage questions.
