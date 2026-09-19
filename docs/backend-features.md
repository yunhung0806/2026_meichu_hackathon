# Identity-first backend

`FridgeService` is the local Python integration layer for the existing application.
The implemented FastAPI station bridge in `fridge_guardian.station_api` now lets
the browser use its identity, operation, inventory, and food-question retrieval
methods. The camera CLI remains available and still uses the original
coordinator directly.

## Reuse and additions

| Capability | Implementation |
| --- | --- |
| Camera, face enrollment and matching | Existing OpenCV / YuNet / SFace |
| Item-instance matching | Existing spatial HSV baseline |
| Users, ownership, templates, events, feedback | Existing SQLite and coordinator |
| Name before action menu | New five-minute local login, face rechecked for operations |
| Private/shared, put time, optional expiry, current inventory | New additive inventory table |
| Expired take-out | Structured warning plus screen/beep feedback |
| Day-before reminders | Persistent owner inbox with acknowledgement |
| Recipe/storage questions | FoodKeeper guidance, Markdown retrieval and optional PN54 Lemonade generation |
| Fine-tuned semantic labels | Model/data unavailable; not implemented or claimed trained |

## UI integration

The supported browser integration is documented in [API_SPEC.md](API_SPEC.md):
`GET /health`, `POST /station/identify`, `POST /station/operate`, and
`POST /questions`, `GET /inventory`, `GET /history`, and `POST /recipes/recommend`, all under `/api/v1`. One Python process owns the camera and
serializes station requests. Tokens stay in the existing in-memory
`FridgeService` store. History and inventory-based recipe questions are connected
to the browser. Reminders remain Python-only; storage questions use POST /questions.
The recipe route uses `RecipeQuestions`: structured ingredient-alias retrieval
over original JSON recipes, expiry prioritization, and optional local generation.
The older `FoodQuestions.ask()` Markdown retrieval below remains available for
Python callers and the storage question route. See API_SPEC.md for the recipe response and model configuration.

The Python example below remains useful for non-HTTP callers and for features
that have not been exposed over HTTP.

```python
from datetime import date
from fridge_guardian.application.fridge_service import FridgeService, PutOptions
from fridge_guardian.adapters.knowledge import LocalKnowledge, FoodQuestions, LemonadeLLM
from fridge_guardian.domain import Action

# Reuse SessionCoordinator(repository, identity, items, feedback).
service = FridgeService(coordinator, timezone="Asia/Taipei")
login = service.identify(face_frames)  # existing capture_session output
print(login.display_name)             # show name, then action menu
result = service.process(login.token, Action.PUT_IN, fresh_operation_frames,
    PutOptions("milk", shared=True, expires_on=date(2026, 9, 22)))
# Produce without a printed date: expires_on=None. UI parses YYYY-MM-DD via date.fromisoformat.
print(service.inventory(login.token))
result = service.process(login.token, Action.TAKE_OUT, fresh_take_frames)
print(result.decision.message, result.warnings)
service.refresh_reminders()  # startup and every 60 seconds on the UI thread
for notice in service.reminders(login.token):
    print(notice)  # display to owner; optionally play audio
    # Call only after the owner dismisses the notification:
    service.acknowledge_reminder(login.token, notice["item_id"], notice["expires_on"])
questions = FoodQuestions(service, LocalKnowledge("data/knowledge"))
for item in questions.storage_guidance(login.token):
    print(item)  # WITHIN_GUIDANCE / USE_SOON / BEYOND_GUIDANCE; never an expiry date
# Once Lemonade and the model are separately installed and running:
# questions.llm = LemonadeLLM(model="Gemma-3-4b-it-GGUF")
answer = questions.ask(login.token, "現在可以煮甚麼？", category="recipes")
answer = questions.ask(login.token, "蘋果如何保存？", category="storage")
print(answer.status, answer.text, answer.passages)
service.logout(login.token)
```

Use one service and repository on their owning UI thread. Do not mix old
coordinator inventory operations with new service operations. Existing items
are not presumed present: their owner must put them in through the new service.
No original tables are dropped. Private non-owner take-out leaves inventory
unchanged. Authorized take-out removes inventory even if expired (e.g. disposal).
This records the selected operation, not proof of actual physical removal.
Item/template/inventory insertion is atomic; event logging and feedback happen
afterward and are not crash-atomic with inventory updates.

Dates use Asia/Taipei by default. A package date expires on the next local day.
Reminders are generated one calendar day before expiry; the process must run
that day. No daemon or external messaging is installed. Poll on the UI thread.
Notices persist across restarts and are owner-only, deduplicated by item/date;
acknowledged notices do not repeat. Removed items are excluded. Tokens are
in-memory and expire after five minutes; log in again after restart.

## Knowledge and models

The bundled `data/knowledge/foodkeeper/common_foods.zh-TW.json` is a reviewed
MVP subset of the public USDA FSIS FoodKeeper dataset. It contains common
refrigerated foods, the source's English guidance, and project-added
Traditional Chinese aliases. The upstream dataset is CC0-1.0 and its catalog
metadata, retrieval date, and source URLs are recorded in the file. Keep those
fields when replacing or expanding the snapshot.

When `expires_on` is absent, `storage_guidance()` deterministically calculates
a reference window from `put_at`. It returns `WITHIN_GUIDANCE`, `USE_SOON`, or
`BEYOND_GUIDANCE` and sorts items needing attention first. It does not populate
`expires_on`, call an LLM for date arithmetic, or claim that food is safe. A
printed package date always takes priority, so those items are excluded from
FoodKeeper guidance. Unmatched labels stay unmatched rather than being guessed.

Add other legally usable UTF-8 Markdown to `data/knowledge/recipes/` and
`data/knowledge/storage/`. Include source URL, version/date and license with
claims in each paragraph. English-token and Chinese-bigram retrieval remains a
lexical baseline, not embedding search. FoodKeeper passages are placed before
matching Markdown passages for storage questions. Returned passages identify
their source. Generated claims and citations still require review. Missing
resources return NO_SOURCES, LLM_NOT_CONFIGURED or LLM_UNAVAILABLE.

The station runtime uses Lemonade's OpenAI-compatible
`/v1/chat/completions` endpoint when `FRIDGE_LEMONADE_MODEL` is set. The base
URL defaults to `http://127.0.0.1:13305/v1`; only loopback HTTP URLs are
accepted, and proxies and redirects are disabled. It sends questions,
retrieved passages, and current owner non-expired item labels; no faces,
embeddings, access tokens, or user IDs. No model is downloaded or trained by
this code. `OllamaLLM` remains available only for non-HTTP legacy callers.

Semantic food classification differs from owned-item matching. Until a labeled
fine-tuned model is supplied, the UI asks for the label via PutOptions. The HSV
baseline cannot name foods. An exported instance recognizer can replace
coordinator.item_recognizer and expose feature_kind; it must reject incompatible
templates. Dataset/license, training, export/preprocessing, checksums, accuracy
and PN54 latency are still required before claiming fine-tuned inference.

## Verification

```bash
PYTHONPATH=src /usr/bin/python3.12 -m unittest tests.test_foodkeeper tests.test_fridge_service tests.test_flow tests.test_policy tests.test_sqlite_repository tests.test_feedback -v
# Full suite on a supported project environment with OpenCV installed:
# python -m unittest discover -s tests -v
```

Tests use fake recognition/LLM, real temporary SQLite and fixed dates. They
cover ownership, identity changes, token expiry, inventory, Taipei dates,
reminder persistence/isolation and missing RAG resources. They do not prove
camera accuracy, training, actual LLM output or PN54 deployment. MLSteam has
Python 3.13.8, PyTorch 2.9.1+ROCm 7.1 and MI300X, while the project declares
Python 3.10–3.12. No packages were changed. Repeat tests on supported Python and
run the physical demo three times on PN54 before shipping.

Verified on MLSteam: the focused Python 3.12 suite passed all 15 tests.
The full suite attempted with /opt/venv/bin/python could not import the
existing item-adapter tests because cv2 is absent. This is an environment
limitation; full camera/model validation remains outstanding.
