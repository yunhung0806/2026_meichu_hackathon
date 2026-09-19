from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np

from fridge_guardian.adapters.knowledge import FoodQuestions, LocalKnowledge
from fridge_guardian.adapters.item_vision import ItemVisionClientError
from fridge_guardian.adapters.sqlite_repository import SQLiteRepository
from fridge_guardian.application import SessionCoordinator
from fridge_guardian.application.fridge_service import FridgeService
from fridge_guardian.application.item_inspection import ItemInspectionManager
from fridge_guardian.domain import FrameSample, utc_now
from fridge_guardian.station_api import StationRuntime, create_app
from tests.fakes import FakeIdentityProvider, FakeItemRecognizer, RecordingFeedback


class FakeCameraCapture:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        session_id = str(uuid4())
        image = np.full((100, 160, 3), 90, dtype=np.uint8)
        return [FrameSample(session_id, utc_now(), image.copy()) for _ in range(3)]


class FakePreviewCamera:
    def __init__(self) -> None:
        self.calls = 0
        self.frame = np.full((100, 160, 3), 90, dtype=np.uint8)

    def __call__(self):
        self.calls += 1
        return self.frame.copy()


class FakeLLM:
    def __init__(self):
        self.prompt = ""

    def generate(self, prompt):
        self.prompt = prompt
        return "建議先處理菠菜，蘋果較耐放。"


class FakeItemVisionClient:
    def __init__(self):
        self.status = "NO_MATCH"
        self.category_status = "OK"
        self.localization_status = "OK"
        self.candidates = []
        self.requests = []

    def infer(self, roi, gallery, *, request_id):
        self.requests.append({"roi": roi.copy(), "gallery": gallery, "request_id": request_id})
        vector = [0.0] * 384
        vector[0] = 1.0
        not_run = {
            "status": "NOT_RUN", "candidates": [], "first_item_id": None,
            "first_similarity": 0.0, "second_item_id": None,
            "second_similarity": 0.0, "margin": 0.0,
        }
        return {
            "protocol_version": "item-vision-v1",
            "request_id": request_id,
            "localization": {"status": self.localization_status, "score": 0.9, "box": [1, 2, 10, 20]},
            "category": {
                "status": self.category_status,
                "top3": [{"label": "beverage", "score": 0.7}],
                "margin": 0.4,
            },
            "instance": {
                "status": self.status,
                "candidates": list(self.candidates),
                "first_item_id": self.candidates[0]["item_id"] if self.candidates else None,
                "first_similarity": self.candidates[0]["similarity"] if self.candidates else 0.0,
                "second_item_id": None, "second_similarity": 0.0, "margin": 0.2,
            },
            "roi_instance": not_run,
            "embeddings": None if self.localization_status != "OK" else {
                "kind": "dinov2-vits14-crop-f32-v1", "dimensions": 384,
                "crop": vector, "roi": vector,
            },
            "models": {},
            "latency_ms": {"localization": 1.0, "category": 2.0, "embedding": 3.0, "total": 6.0},
        }


class StationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = SQLiteRepository(Path(self.tempdir.name) / "station.db")
        self.identity = FakeIdentityProvider()
        self.items = FakeItemRecognizer()
        self.feedback = RecordingFeedback()
        coordinator = SessionCoordinator(
            self.repo, self.identity, self.items, self.feedback
        )
        self.service = FridgeService(coordinator)
        self.capture = FakeCameraCapture()
        self.preview = FakePreviewCamera()
        self.item_vision = FakeItemVisionClient()
        self.inspections = ItemInspectionManager(self.service, self.item_vision)
        questions = FoodQuestions(self.service, LocalKnowledge(self.tempdir.name))
        self.warning_audio = Path(self.tempdir.name) / "warning.mp3"
        self.warning_audio.write_bytes(b"test audio")
        runtime = StationRuntime(
            self.service, self.capture, questions=questions,
            preview_frame=self.preview,
            item_inspections=self.inspections,
            warning_audio_path=self.warning_audio,
        )
        self.app = create_app(runtime, allowed_origins=["http://localhost:3000"])
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver"
        )
        self.owner = self.repo.add_user("Owner")
        self.other = self.repo.add_user("Other")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.repo.close()
        self.tempdir.cleanup()

    async def identify(self, user):
        self.identity.user_id = user.user_id
        response = await self.client.post("/api/v1/station/identify")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["user_id"], user.user_id)
        return data["access_token"]

    @staticmethod
    def auth(token):
        return {"Authorization": f"Bearer {token}"}

    async def put(
        self, token, *, label="apple", shared=False, shared_user_ids=None,
        expires_on=None,
    ):
        self.identity.user_id = self.owner.user_id
        self.item_vision.status = "NO_MATCH"
        self.item_vision.candidates = []
        inspected = await self.client.post(
            "/api/v1/station/inspect",
            headers=self.auth(token),
            json={"action": "PUT_IN"},
        )
        self.assertEqual(inspected.status_code, 200, inspected.text)
        response = await self.client.post(
            "/api/v1/station/operate",
            headers=self.auth(token),
            json={
                "inspection_id": inspected.json()["data"]["inspection_id"],
                "action": "PUT_IN", "confirmed": True, "label": label,
                "add_as_new": True, "shared": shared,
                "shared_user_ids": shared_user_ids or [],
                "expires_on": expires_on,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["decision"], "ITEM_REGISTERED")
        return data

    async def test_health_and_successful_identification(self):
        response = await self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["camera_owner"], "python")
        token = await self.identify(self.owner)
        self.assertTrue(token)
        self.assertEqual(self.capture.calls, 1)
        members = await self.client.get(
            "/api/v1/members", headers=self.auth(token)
        )
        self.assertEqual(members.status_code, 200, members.text)
        self.assertEqual(
            members.json()["data"]["users"],
            [{"user_id": self.other.user_id, "display_name": "Other"}],
        )

        audio = await self.client.get(
            "/api/v1/station/warning-audio", headers=self.auth(token)
        )
        self.assertEqual(audio.status_code, 200, audio.text)
        self.assertEqual(audio.content, b"test audio")
        self.assertEqual(audio.headers["cache-control"], "no-store")

    async def test_warning_audio_requires_login(self):
        response = await self.client.get("/api/v1/station/warning-audio")
        self.assertEqual(response.status_code, 401)

    async def test_selected_share_is_visible_and_takeable_only_by_recipient(self):
        stranger = self.repo.add_user("Stranger")
        owner_token = await self.identify(self.owner)
        stored = await self.put(
            owner_token,
            label="shared yogurt",
            shared_user_ids=[self.other.user_id],
        )

        other_token = await self.identify(self.other)
        other_items = (await self.client.get(
            "/api/v1/inventory", headers=self.auth(other_token)
        )).json()["data"]["items"]
        self.assertEqual([item["item_id"] for item in other_items], [stored["item_id"]])
        self.assertEqual(other_items[0]["owner_name"], "Owner")
        self.assertEqual(other_items[0]["access_type"], "SHARED_DIRECT")

        stranger_token = await self.identify(stranger)
        stranger_items = (await self.client.get(
            "/api/v1/inventory", headers=self.auth(stranger_token)
        )).json()["data"]["items"]
        self.assertEqual([item["item_id"] for item in stranger_items], [stored["item_id"]])
        self.assertEqual(stranger_items[0]["access_type"], "PRIVATE_VISIBLE")
        self.assertFalse(stranger_items[0]["can_take"])
        self.assertFalse(stranger_items[0]["can_edit"])

        self.identity.user_id = self.other.user_id
        self.item_vision.status = "MATCHED"
        self.item_vision.candidates = [
            {"item_id": stored["item_id"], "similarity": 0.94}
        ]
        inspection = (await self.client.post(
            "/api/v1/station/inspect",
            headers=self.auth(other_token),
            json={"action": "TAKE_OUT"},
        )).json()["data"]
        response = await self.client.post(
            "/api/v1/station/operate",
            headers=self.auth(other_token),
            json={
                "inspection_id": inspection["inspection_id"],
                "action": "TAKE_OUT",
                "confirmed": True,
                "selected_item_id": stored["item_id"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["decision"], "ALLOW_SHARED")

    async def test_inventory_visibility_permissions_and_owner_edits(self):
        owner_token = await self.identify(self.owner)
        private = await self.put(
            owner_token, label=" private milk ", expires_on="2026-09-25"
        )
        shared = await self.put(owner_token, label="shared tea", shared=True)
        other_token = await self.identify(self.other)

        visible = (await self.client.get(
            "/api/v1/inventory", headers=self.auth(other_token)
        )).json()["data"]["items"]
        by_id = {item["item_id"]: item for item in visible}
        self.assertEqual(set(by_id), {private["item_id"], shared["item_id"]})
        self.assertFalse(by_id[private["item_id"]]["can_take"])
        self.assertFalse(by_id[private["item_id"]]["can_edit"])
        self.assertTrue(by_id[shared["item_id"]]["can_take"])
        self.assertFalse(by_id[shared["item_id"]]["can_edit"])

        edited = await self.client.patch(
            f"/api/v1/inventory/{private['item_id']}",
            headers=self.auth(owner_token),
            json={"label": "milk tea", "expires_on": "2026-10-02", "shared": True},
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["data"]["label"], "milk tea")
        self.assertEqual(edited.json()["data"]["expires_on"], "2026-10-02")
        self.assertTrue(edited.json()["data"]["shared"])
        self.assertTrue(edited.json()["data"]["can_edit"])

        cleared = await self.client.patch(
            f"/api/v1/inventory/{private['item_id']}",
            headers=self.auth(owner_token),
            json={"expires_on": None, "shared": False},
        )
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertIsNone(cleared.json()["data"]["expires_on"])
        self.assertFalse(cleared.json()["data"]["shared"])
        after_private = (await self.client.get(
            "/api/v1/inventory", headers=self.auth(other_token)
        )).json()["data"]["items"]
        private_row = next(item for item in after_private if item["item_id"] == private["item_id"])
        self.assertFalse(private_row["can_take"])

    async def test_inventory_edit_rejects_nonowner_unknown_and_forbidden_fields(self):
        owner_token = await self.identify(self.owner)
        stored = await self.put(owner_token)
        other_token = await self.identify(self.other)

        forbidden = await self.client.patch(
            f"/api/v1/inventory/{stored['item_id']}",
            headers=self.auth(other_token), json={"label": "stolen name"},
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.text)
        unknown = await self.client.patch(
            f"/api/v1/inventory/{uuid4()}",
            headers=self.auth(owner_token), json={"label": "unknown"},
        )
        self.assertEqual(unknown.status_code, 404, unknown.text)
        for payload in (
            {},
            {"owner_id": self.other.user_id},
            {"item_id": "replacement"},
            {"present": False},
            {"label": "   "},
        ):
            response = await self.client.patch(
                f"/api/v1/inventory/{stored['item_id']}",
                headers=self.auth(owner_token), json=payload,
            )
            self.assertEqual(response.status_code, 422, (payload, response.text))
        current = self.repo.get_item(stored["item_id"])
        self.assertEqual(current.owner_id, self.owner.user_id)
        self.assertEqual(current.label, "apple")

    async def test_setting_private_revokes_direct_shares(self):
        owner_token = await self.identify(self.owner)
        stored = await self.put(
            owner_token, label="shared yogurt",
            shared_user_ids=[self.other.user_id],
        )
        self.assertTrue(self.repo.is_shared_with(stored["item_id"], self.other.user_id))
        response = await self.client.patch(
            f"/api/v1/inventory/{stored['item_id']}",
            headers=self.auth(owner_token), json={"shared": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.repo.is_shared_with(stored["item_id"], self.other.user_id))
        other_token = await self.identify(self.other)
        row = (await self.client.get(
            "/api/v1/inventory", headers=self.auth(other_token)
        )).json()["data"]["items"][0]
        self.assertFalse(row["can_take"])

    async def test_same_label_records_keep_independent_identity_and_expiry_order(self):
        token = await self.identify(self.owner)
        late = await self.put(token, label="麥香", expires_on="2026-10-02")
        early = await self.put(token, label="麥香", expires_on="2026-09-23")
        middle = await self.put(token, label="麥香", expires_on="2026-09-25")
        rows = (await self.client.get(
            "/api/v1/inventory", headers=self.auth(token)
        )).json()["data"]["items"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({row["item_id"] for row in rows}), 3)
        self.assertEqual(
            [row["item_id"] for row in rows],
            [early["item_id"], middle["item_id"], late["item_id"]],
        )
        self.assertEqual(
            [row["expires_on"] for row in rows],
            ["2026-09-23", "2026-09-25", "2026-10-02"],
        )

        self.identity.user_id = self.owner.user_id
        self.item_vision.status = "AMBIGUOUS"
        self.item_vision.candidates = [
            {"item_id": late["item_id"], "similarity": 0.91},
            {"item_id": early["item_id"], "similarity": 0.90},
        ]
        inspection = (await self.client.post(
            "/api/v1/station/inspect", headers=self.auth(token),
            json={"action": "TAKE_OUT"},
        )).json()["data"]
        result = await self.client.post(
            "/api/v1/station/operate", headers=self.auth(token), json={
                "inspection_id": inspection["inspection_id"],
                "action": "TAKE_OUT", "confirmed": True,
                "selected_item_id": middle["item_id"],
            },
        )
        self.assertEqual(result.status_code, 200, result.text)
        states = dict(self.repo.connection.execute(
            "SELECT item_id, present FROM inventory WHERE item_id IN (?, ?, ?)",
            (late["item_id"], early["item_id"], middle["item_id"]),
        ).fetchall())
        self.assertEqual(states, {
            late["item_id"]: 1,
            early["item_id"]: 1,
            middle["item_id"]: 0,
        })

    async def test_camera_preview_is_uncached_and_draws_item_roi(self):
        import cv2

        response = await self.client.get("/api/v1/station/preview")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertTrue(response.content.startswith(b"\xff\xd8"))
        image = cv2.imdecode(np.frombuffer(response.content, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(image.shape[:2], (100, 160))
        blue, green, red = image[28, 88]
        self.assertGreater(int(green), int(blue) + 30)
        self.assertGreater(int(green), int(red) + 30)
        self.assertEqual(self.preview.calls, 1)
        self.assertEqual(self.capture.calls, 0)
        self.assertTrue(np.all(self.preview.frame == 90))

    async def test_browser_enrollment_creates_user_and_login(self):
        response = await self.client.post(
            "/api/v1/station/enroll", json={"display_name": "  New User  "}
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["display_name"], "New User")
        enrolled = [user for user in self.repo.list_users() if user.user_id == data["user_id"]]
        self.assertEqual(len(enrolled), 1)
        self.assertEqual(len(self.repo.list_face_templates()), 3)
        inventory = await self.client.get(
            "/api/v1/inventory", headers=self.auth(data["access_token"])
        )
        self.assertEqual(inventory.status_code, 200)
        self.assertEqual(inventory.json()["data"]["items"], [])

        blank = await self.client.post(
            "/api/v1/station/enroll", json={"display_name": "   "}
        )
        self.assertEqual(blank.status_code, 422)

    async def test_recipe_recommendations_use_authenticated_inventory(self):
        token = await self.identify(self.owner)
        await self.put(token, label="菠菜")
        response = await self.client.post(
            "/api/v1/recipes/recommend",
            headers=self.auth(token),
            json={"question": "請推薦可以先處理菠菜的料理"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertTrue(data["recipes"])
        self.assertIn("菠菜", data["recipes"][0]["matched"])
        self.assertEqual(data["status"], "RETRIEVAL_ONLY")

        denied = await self.client.post(
            "/api/v1/recipes/recommend",
            headers=self.auth("invalid"),
            json={"question": "推薦料理"},
        )
        self.assertEqual(denied.status_code, 401)

        other_token = await self.identify(self.other)
        visible = await self.client.get(
            "/api/v1/inventory", headers=self.auth(other_token)
        )
        self.assertEqual(len(visible.json()["data"]["items"]), 1)
        scoped = await self.client.post(
            "/api/v1/recipes/recommend",
            headers=self.auth(other_token),
            json={"question": "請推薦可以先處理菠菜的料理"},
        )
        self.assertEqual(scoped.status_code, 200, scoped.text)
        self.assertEqual(scoped.json()["data"]["status"], "EMPTY_INVENTORY")
        self.assertEqual(scoped.json()["data"]["ingredients"], [])

        guidance = self.app.state.station.questions.storage_guidance(other_token)
        self.assertEqual(guidance, ())

    async def test_put_in_inventory_and_owner_take_out(self):
        token = await self.identify(self.owner)
        stored = await self.put(token, label="confirmed milk")
        inventory = await self.client.get(
            "/api/v1/inventory", headers=self.auth(token)
        )
        self.assertEqual(inventory.status_code, 200)
        inventory_item = inventory.json()["data"]["items"][0]
        self.assertEqual(inventory_item["label"], "confirmed milk")
        self.assertEqual(inventory_item["owner_name"], "Owner")

        self.identity.user_id = self.owner.user_id
        self.item_vision.status = "MATCHED"
        self.item_vision.candidates = [{"item_id": stored["item_id"], "similarity": 0.91}]
        inspected = await self.client.post(
            "/api/v1/station/inspect", headers=self.auth(token),
            json={"action": "TAKE_OUT"},
        )
        response = await self.client.post(
            "/api/v1/station/operate",
            headers=self.auth(token),
            json={
                "inspection_id": inspected.json()["data"]["inspection_id"],
                "action": "TAKE_OUT", "confirmed": True,
                "selected_item_id": stored["item_id"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["outcome"], "ALLOW")
        self.assertEqual(data["decision"], "ALLOW_OWNER")
        self.assertEqual(data["item_id"], stored["item_id"])
        self.assertEqual(
            (await self.client.get("/api/v1/inventory", headers=self.auth(token))).json()["data"]["items"],
            [],
        )
        history = await self.client.get("/api/v1/history", headers=self.auth(token))
        self.assertEqual(history.status_code, 200, history.text)
        events = history.json()["data"]["events"]
        self.assertEqual([event["action"] for event in events], ["TAKE_OUT", "PUT_IN"])
        self.assertEqual([event["decision"] for event in events], ["ALLOW_OWNER", "ITEM_REGISTERED"])
        self.assertEqual({event["item_label"] for event in events}, {"confirmed milk"})

        other_token = await self.identify(self.other)
        other_history = await self.client.get(
            "/api/v1/history", headers=self.auth(other_token)
        )
        self.assertEqual(other_history.json()["data"]["events"], [])

    async def test_unauthorized_take_out_is_not_selectable_and_injection_is_rejected(self):
        owner_token = await self.identify(self.owner)
        stored = await self.put(owner_token)
        other_token = await self.identify(self.other)

        self.identity.user_id = self.other.user_id
        self.item_vision.status = "MATCHED"
        self.item_vision.candidates = [{"item_id": stored["item_id"], "similarity": 0.94}]
        inspected = await self.client.post(
            "/api/v1/station/inspect",
            headers=self.auth(other_token),
            json={"action": "TAKE_OUT"},
        )
        self.assertEqual(inspected.status_code, 200)
        inspection = inspected.json()["data"]
        self.assertEqual(inspection["instance"]["candidates"], [])
        self.assertEqual(inspection["review_state"], "WARN_NOT_OWNER")
        self.assertEqual(inspection["review_decision"], "WARN_NOT_OWNER")
        self.assertEqual(self.feedback.decisions[-1].code.value, "WARN_NOT_OWNER")
        actor_events = (await self.client.get(
            "/api/v1/history", headers=self.auth(other_token)
        )).json()["data"]["events"]
        owner_events = (await self.client.get(
            "/api/v1/history", headers=self.auth(owner_token)
        )).json()["data"]["events"]
        self.assertEqual(actor_events[0]["decision"], "WARN_NOT_OWNER")
        self.assertEqual(actor_events[0]["viewer_role"], "ACTOR")
        self.assertEqual(actor_events[0]["related_user_name"], "Owner")
        owner_warning = next(
            event for event in owner_events if event["decision"] == "WARN_NOT_OWNER"
        )
        self.assertEqual(owner_warning["viewer_role"], "OWNER")
        self.assertEqual(owner_warning["related_user_name"], "Other")
        response = await self.client.post(
            "/api/v1/station/operate",
            headers=self.auth(other_token),
            json={
                "inspection_id": inspection["inspection_id"],
                "action": "TAKE_OUT", "confirmed": True,
                "selected_item_id": stored["item_id"],
            },
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"]["code"], "INVALID_ITEM_SELECTION")
        owner_inventory = (await self.client.get(
            "/api/v1/inventory", headers=self.auth(owner_token)
        )).json()["data"]["items"]
        self.assertEqual(len(owner_inventory), 1)

    async def test_inspection_without_authorized_inventory_cannot_commit(self):
        token = await self.identify(self.owner)
        self.identity.user_id = self.owner.user_id
        self.item_vision.status = "MATCHED"
        self.item_vision.candidates = [{"item_id": "injected", "similarity": 0.99}]
        inspected = await self.client.post(
            "/api/v1/station/inspect",
            headers=self.auth(token),
            json={"action": "TAKE_OUT"},
        )
        self.assertEqual(inspected.status_code, 200, inspected.text)
        inspection = inspected.json()["data"]
        self.assertEqual(inspection["authorized_inventory"], [])
        self.assertEqual(inspection["review_state"], "NO_AUTHORIZED_ITEMS")
        self.assertFalse(inspection["committable"])
        response = await self.client.post(
            "/api/v1/station/operate", headers=self.auth(token),
            json={
                "inspection_id": inspection["inspection_id"],
                "action": "TAKE_OUT", "confirmed": True,
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "NO_AUTHORIZED_ITEMS")
        self.assertEqual(
            (await self.client.get("/api/v1/inventory", headers=self.auth(token))).json()["data"]["items"],
            [],
        )
        invalid = await self.client.get(
            "/api/v1/inventory", headers=self.auth("not-a-token")
        )
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(invalid.json()["error"]["code"], "UNAUTHORIZED")
        invalid_operation = await self.client.post(
            "/api/v1/station/operate",
            headers=self.auth("not-a-token"),
            json={
                "inspection_id": inspected.json()["data"]["inspection_id"],
                "action": "TAKE_OUT", "confirmed": True,
            },
        )
        self.assertEqual(invalid_operation.status_code, 401)
        self.assertEqual(invalid_operation.json()["error"]["code"], "UNAUTHORIZED")
        self.assertEqual(self.capture.calls, 2)  # identify + inspect; invalid token captures nothing

    async def test_put_in_requires_user_confirmed_label(self):
        token = await self.identify(self.owner)
        self.identity.user_id = self.owner.user_id
        inspected = await self.client.post(
            "/api/v1/station/inspect", headers=self.auth(token),
            json={"action": "PUT_IN"},
        )
        response = await self.client.post(
            "/api/v1/station/operate",
            headers=self.auth(token),
            json={
                "inspection_id": inspected.json()["data"]["inspection_id"],
                "action": "PUT_IN", "confirmed": True, "label": "   ",
                "add_as_new": True,
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    async def test_item_service_unavailable_is_clear_and_non_mutating(self):
        token = await self.identify(self.owner)
        self.identity.user_id = self.owner.user_id
        before = self.repo.connection.total_changes

        def unavailable(*_args, **_kwargs):
            raise ItemVisionClientError("connection refused")

        self.item_vision.infer = unavailable
        response = await self.client.post(
            "/api/v1/station/inspect", headers=self.auth(token),
            json={"action": "PUT_IN"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "ITEM_VISION_UNAVAILABLE")
        self.assertEqual(self.repo.connection.total_changes, before)

    async def test_foodkeeper_question_uses_authenticated_inventory(self):
        token = await self.identify(self.owner)
        await self.put(token, label="apple")
        response = await self.client.post(
            "/api/v1/questions",
            headers=self.auth(token),
            json={"question": "蘋果可以冷藏多久？", "category": "storage"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["status"], "LLM_NOT_CONFIGURED")
        self.assertTrue(data["sources"])
        self.assertEqual(data["sources"][0]["source"], "foodkeeper/蘋果")
        self.assertIn("一般冷藏保存指引", data["sources"][0]["text"])

    async def test_specific_food_question_has_source_without_inventory(self):
        token = await self.identify(self.owner)
        response = await self.client.post(
            "/api/v1/questions",
            headers=self.auth(token),
            json={"question": "蘋果可以冷藏多久？"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["status"], "LLM_NOT_CONFIGURED")
        self.assertEqual(data["sources"][0]["source"], "foodkeeper/蘋果")

    async def test_question_returns_generated_answer_when_llm_is_configured(self):
        token = await self.identify(self.owner)
        await self.put(token, label="菠菜")
        llm = FakeLLM()
        self.app.state.station.questions.llm = llm
        response = await self.client.post(
            "/api/v1/questions",
            headers=self.auth(token),
            json={"question": "哪些食物要先處理？"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["status"], "OK")
        self.assertEqual(data["answer"], "建議先處理菠菜，蘋果較耐放。")
        self.assertIn("foodkeeper/菠菜", llm.prompt)

    async def test_question_requires_valid_login_and_question(self):
        missing = await self.client.post(
            "/api/v1/questions", json={"question": "如何保存蘋果？"}
        )
        self.assertEqual(missing.status_code, 401)
        token = await self.identify(self.owner)
        blank = await self.client.post(
            "/api/v1/questions", headers=self.auth(token), json={"question": "   "}
        )
        self.assertEqual(blank.status_code, 422)
        self.assertEqual(blank.json()["error"]["code"], "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()
