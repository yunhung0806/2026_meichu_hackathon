# Fridge Treaty — API Specification

**Version:** 1.0.0 (proposed contract)  
**Status:** Team agreement required; examples are illustrative, not implemented endpoints.  
**Base path:** `/api/v1`

This document is the shared contract for frontend, FastAPI backend, face recognition, and object recognition. The backend exposes all HTTP endpoints; recognition teams may deliver Python functions for backend integration rather than hosting separate services.

## 1. General conventions

| Item | Contract |
| --- | --- |
| Requests | JSON (`application/json`), except image uploads (`multipart/form-data`) |
| Responses | JSON: `{"success": true, "data": ...}` on success; `{"success": false, "error": ...}` on error |
| Date | `YYYY-MM-DD` (calendar date, not timestamp) |
| Timestamp | UTC ISO 8601, e.g. `2026-09-19T07:00:00Z` |
| User identifier | Opaque string, e.g. `U001` |
| Item identifier | Opaque string, e.g. `F001`; **one record per physical item** |
| Authentication | `Authorization: Bearer <access_token>` on protected endpoints |
| Permissions | `PRIVATE`, `SHARED`, `ASK_FIRST` |
| Item status | `IN_FRIDGE`, `REMOVED` |
| Recognition score | Numeric 0–1 if available; a model score is **not necessarily a calibrated probability** |

**Security and data rules:** Recognition is a candidate identification, not authentication. To obtain a session, confirm identity using a PIN or another independently verified method. The backend derives the acting `user_id` and new item's `owner_id` from the authenticated session, never from a client-supplied owner field. Keep biometric photos/templates and PIN material out of Git; do not expose them in normal API responses. Use HTTPS outside a trusted local prototype. The camera station captures a registration/removal interaction; without additional sensors, the system cannot prove an item physically entered or left a refrigerator.

**Suggested session behavior:** A session expires after 1,800 seconds; recognition challenge expires after 60 seconds. These are proposed MVP configuration values, not fixed model requirements.

## 2. Endpoint index

| Method | Route | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/api/v1/face/recognize` | No | Produce candidate identity from face photo |
| POST | `/api/v1/auth/confirm` | No; valid recognition challenge + PIN required | Confirm identity and issue session |
| POST | `/api/v1/objects/recognize` | Yes | Recognize food for `INSERT` or identify candidate registered items for `REMOVE` |
| POST | `/api/v1/items` | Yes | Register a single physical item |
| GET | `/api/v1/items` | Yes | List items currently in fridge |
| POST | `/api/v1/items/{item_id}/check-removal` | Yes | Check whether current user may remove item; no mutation |
| POST | `/api/v1/items/{item_id}/remove` | Yes | Recheck permission and mark item removed atomically |

## 3. Face recognition

### `POST /api/v1/face/recognize`

**Content-Type:** `multipart/form-data`. Required field `image`: one JPEG/PNG file. Backend should enforce allowed MIME types and upload size (agree on actual limit during implementation).

**200 — candidate recognized:**

```json
{
  "success": true,
  "data": {
    "candidate_user_id": "U001",
    "display_name": "Alice",
    "confidence": 0.95,
    "recognition_token": "example-short-lived-challenge",
    "expires_in": 60
  }
}
```

**200 — no matching user:**

```json
{
  "success": true,
  "data": {
    "candidate_user_id": null,
    "display_name": null,
    "confidence": 0.31,
    "recognition_token": null,
    "expires_in": 0
  }
}
```

A `recognition_token` must be random, generated and stored/verified server-side, short-lived, single-use, and bound to the candidate user. Do not send face embeddings to the frontend. No detectable face may produce `422 FACE_NOT_DETECTED` in the shared error format.

### `POST /api/v1/auth/confirm`

**Content-Type:** `application/json`

```json
{
  "recognition_token": "example-short-lived-challenge",
  "pin": "123456"
}
```

**200:**

```json
{
  "success": true,
  "data": {
    "user_id": "U001",
    "display_name": "Alice",
    "access_token": "example-random-session-token",
    "token_type": "bearer",
    "expires_in": 1800
  }
}
```

PINs must be stored using a suitable password/PIN hash, never plaintext. Apply attempt limits; invalid/expired/used challenges and wrong PINs must not create sessions (`401 INVALID_CREDENTIALS`). Only a successfully confirmed session can establish item ownership.

## 4. Object recognition

### `POST /api/v1/objects/recognize`

**Authorization:** Bearer session. **Content-Type:** `multipart/form-data`.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `image` | JPEG/PNG file | Yes | Food photo |
| `mode` | string | Yes | Exactly `INSERT` or `REMOVE` |

**200 — INSERT:** class prediction only; does **not** register an item.

```json
{
  "success": true,
  "data": {
    "mode": "INSERT",
    "food_name": "milk",
    "confidence": 0.94,
    "image_ref": "IMG001",
    "candidates": [
      {"food_name": "milk", "confidence": 0.94},
      {"food_name": "yogurt", "confidence": 0.04}
    ]
  }
}
```

`image_ref` is an opaque reference issued by the backend to a privately stored temporary image. It is not an arbitrary client filesystem path. The user must confirm/edit the food name and enter expiration date and permission before creating an item.

**200 — REMOVE:** return candidate registered physical items only, not an automatic final identification.

```json
{
  "success": true,
  "data": {
    "mode": "REMOVE",
    "food_name": "milk",
    "confidence": 0.94,
    "image_ref": "IMG002",
    "candidates": [
      {"item_id": "F001", "food_name": "milk", "owner_id": "U001", "similarity": 0.96},
      {"item_id": "F002", "food_name": "milk", "owner_id": "U002", "similarity": 0.88}
    ]
  }
}
```

If the current model performs only **category classification**, the backend may query `IN_FRIDGE` items by `food_name`. Set `similarity: null` for those candidates; do not invent image-matching scores. If none are available, return `"candidates": []`. Classifier `confidence` and instance-match `similarity` represent different quantities. The frontend must ask the user to select/confirm an `item_id`. Identical packaging may require a physical item label or another manual distinction.

## 5. Item registration (INSERT)

### `POST /api/v1/items`

**Authorization:** Bearer session. **Content-Type:** `application/json`.

```json
{
  "food_name": "milk",
  "expiration_date": "2026-09-22",
  "permission": "PRIVATE",
  "image_ref": "IMG001"
}
```

| Field | Type | Required | Validation |
| --- | --- | --- | --- |
| `food_name` | nonempty string | Yes | Confirmed by user; trim whitespace |
| `expiration_date` | `YYYY-MM-DD` or `null` | Yes | `null` when unknown; don't fabricate dates |
| `permission` | enum | Yes | `PRIVATE`, `SHARED`, or `ASK_FIRST` |
| `image_ref` | string or `null` | Yes | If present, must refer to a valid server-owned temporary image |

The client must **not** set `owner_id`, `item_id`, `status`, or `created_at`. One request registers one physical item. The backend validates the session, generates a unique item ID, sets owner to authenticated user, stores the item, and records an `INSERT` event in a database transaction.

**201 Created:**

```json
{
  "success": true,
  "data": {
    "item_id": "F001",
    "food_name": "milk",
    "owner_id": "U001",
    "expiration_date": "2026-09-22",
    "permission": "PRIVATE",
    "status": "IN_FRIDGE",
    "image_ref": "IMG001",
    "created_at": "2026-09-19T07:00:00Z"
  }
}
```

Permission meanings:

- `PRIVATE`: owner only.
- `SHARED`: any authenticated roommate may remove.
- `ASK_FIRST`: owner may remove; others are blocked until an authorization feature is explicitly implemented. A future request feature must not be inferred from this enum alone.

An expiration date represents user-entered package information, not a system guarantee of food safety. An optional quantity field is **not** in v1: register separate items individually to preserve ownership identity.

## 6. Item removal (REMOVE)

The frontend first confirms a candidate `item_id`, checks permission, then asks the user for final confirmation. The second endpoint **must recheck** all conditions because the item status/permission may have changed.

### `POST /api/v1/items/{item_id}/check-removal`

**Authorization:** Bearer session. **Body:** none. Does not mutate inventory.

**200 — owner:**

```json
{
  "success": true,
  "data": {
    "item_id": "F001",
    "allowed": true,
    "reason": "OWNER",
    "owner_id": "U001",
    "permission": "PRIVATE"
  }
}
```

**200 — shared:**

```json
{
  "success": true,
  "data": {
    "item_id": "F001",
    "allowed": true,
    "reason": "SHARED",
    "owner_id": "U001",
    "permission": "SHARED"
  }
}
```

**200 — other user's private item:**

```json
{
  "success": true,
  "data": {
    "item_id": "F001",
    "allowed": false,
    "reason": "NOT_OWNER",
    "owner_id": "U001",
    "permission": "PRIVATE"
  }
}
```

**200 — ask first:**

```json
{
  "success": true,
  "data": {
    "item_id": "F001",
    "allowed": false,
    "reason": "PERMISSION_REQUIRED",
    "owner_id": "U001",
    "permission": "ASK_FIRST"
  }
}
```

`allowed: false` is a successful permission **query**, so it returns HTTP 200, not 403. Missing item: `404 ITEM_NOT_FOUND`; already removed: `409 ITEM_ALREADY_REMOVED`.

### `POST /api/v1/items/{item_id}/remove`

**Authorization:** Bearer session. **Content-Type:** `application/json`.

```json
{"confirmed": true}
```

Require `confirmed: true`; otherwise reject request (`400 INVALID_REQUEST`). Recheck authenticated actor, existence, `IN_FRIDGE` status, and current permission **inside a database transaction**. Atomically set status to `REMOVED` and write a `REMOVE` event. Never delete the item record. Only the first valid removal can succeed.

**200:**

```json
{
  "success": true,
  "data": {
    "item_id": "F001",
    "status": "REMOVED",
    "removed_by": "U001",
    "removed_at": "2026-09-19T07:10:00Z"
  }
}
```

No permission: `403 PERMISSION_DENIED`; missing item: `404 ITEM_NOT_FOUND`; already removed: `409 ITEM_ALREADY_REMOVED`.

## 7. Inventory lookup (required for removal UI)

### `GET /api/v1/items`

**Authorization:** Bearer session. **Body:** none. Returns items with status `IN_FRIDGE` only, unless a future version specifies filters.

**200:**

```json
{
  "success": true,
  "data": {
    "items": [
      {
        "item_id": "F001",
        "food_name": "milk",
        "owner_id": "U001",
        "expiration_date": "2026-09-22",
        "permission": "PRIVATE",
        "status": "IN_FRIDGE"
      },
      {
        "item_id": "F002",
        "food_name": "apple",
        "owner_id": "U002",
        "expiration_date": null,
        "permission": "SHARED",
        "status": "IN_FRIDGE"
      }
    ]
  }
}
```

Do not include biometric information, PIN hashes, or private image paths. If user visibility restrictions are added, enforce them on the backend.

## 8. Standard errors

**All non-2xx responses** use:

```json
{
  "success": false,
  "error": {
    "code": "ITEM_NOT_FOUND",
    "message": "The requested item does not exist."
  }
}
```

| HTTP status | Code | Meaning |
| --- | --- | --- |
| 400 | `INVALID_REQUEST` | Semantically invalid request, e.g. `confirmed` not true |
| 401 | `UNAUTHORIZED` | Missing/invalid/expired session |
| 401 | `INVALID_CREDENTIALS` | Bad PIN, challenge, or challenge expiry |
| 403 | `PERMISSION_DENIED` | User cannot remove requested item |
| 404 | `ITEM_NOT_FOUND` | No such item |
| 409 | `ITEM_ALREADY_REMOVED` | Item already out of stock |
| 422 | `FACE_NOT_DETECTED` | No face detectable in submitted photo |
| 422 | `VALIDATION_ERROR` | Missing field, invalid enum/type/date, malformed upload |
| 500 | `INTERNAL_ERROR` | Unexpected server failure |

A model that runs successfully but returns no match should ordinarily respond `200` with null candidate / empty candidates, not a server error. FastAPI should implement common exception handlers so Pydantic errors comply with this envelope; do not leak tracebacks to clients.

## 9. End-to-end request sequences

### INSERT

1. `POST /face/recognize` → candidate + recognition challenge.
2. `POST /auth/confirm` → authenticated access token.
3. `POST /objects/recognize` with `mode=INSERT` → food candidate + image reference.
4. Frontend displays editable food name, expiration date, and permission; user confirms.
5. `POST /items` with session → creates item tied to authenticated owner.

### REMOVE

1. Reuse valid access token; if absent, perform face recognition + confirmation.
2. `POST /objects/recognize` with `mode=REMOVE` → item candidates.
3. User selects the actual `item_id` (or resolves ambiguity via label).
4. `POST /items/{item_id}/check-removal` → permission display.
5. If allowed, user confirms and calls `POST /items/{item_id}/remove` → inventory update + event.

All route fragments above are relative to `/api/v1`.

## 10. Suggested database contract

| Table | Columns / purpose |
| --- | --- |
| `users` | `user_id` PK, `display_name`, `created_at` |
| `items` | `item_id` PK, `food_name`, `owner_id` FK, `expiration_date` nullable, `permission`, `status`, `image_ref` nullable, `created_at` |
| `events` | `event_id` PK, `item_id` FK, `user_id` FK, `action` (`INSERT`/`REMOVE`), `timestamp` |
| auth storage (private) | PIN verifier, recognition challenge, session token records; not exposed through ordinary inventory API |

Use SQL constraints for permission/status and foreign keys. Use transactions to keep status and event consistent. Store face embeddings/photos separately with controlled access and a defined retention/deletion policy.

## 11. Team handoff and change control

| Owner | Deliverable |
| --- | --- |
| Face team | Python recognition adapter producing candidate user + score; backend issues recognition challenge |
| Object team | Python recognition adapter for both modes; scores and candidate items genuinely reflect supported model capability |
| Backend team | FastAPI routes, Pydantic request/response schemas, auth/session, SQLite, error handlers, generated `/openapi.json` |
| Frontend team | Two distinct INSERT/REMOVE screens; image upload, candidate confirmation, expiration + permission form, authenticated API calls, error states |

**Single source of truth:** Commit this document to `docs/API_SPEC.md`. The backend Pydantic models and its `/openapi.json` must match it. Frontend may use sample JSON as mocks, but mocks are not a substitute for contract tests. Any field/route changes require updating the document and informing all teammates in the same PR.

**Minimum acceptance tests:** face no-match and valid candidate; invalid PIN; object INSERT and REMOVE with ambiguous items; owner insertion; owner removal; shared removal; private and ask-first denial; duplicate removal conflict; unknown expiration date; invalid permission; expired session. Use test data only; sample IDs/scores/tokens in this document are illustrative.

**Scope note:** This contract covers the current five-feature MVP. Competition-specific MI300 usage and fine-tuning/RAG requirements require a separate, genuine implementation and evidence plan; they are not fulfilled merely by these endpoints.
