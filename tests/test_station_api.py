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
        coordinator = SessionCoordinator(
            self.repo, self.identity, self.items, RecordingFeedback()
        )
        self.service = FridgeService(coordinator)
        self.capture = FakeCameraCapture()
        self.item_vision = FakeItemVisionClient()
        self.inspections = ItemInspectionManager(self.service, self.item_vision)
        questions = FoodQuestions(self.service, LocalKnowledge(self.tempdir.name))
        runtime = StationRuntime(
            self.service, self.capture, questions=questions,
            item_inspections=self.inspections,
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

    async def put(self, token, *, label="apple", shared=False):
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

    async def test_put_in_inventory_and_owner_take_out(self):
        token = await self.identify(self.owner)
        stored = await self.put(token, label="confirmed milk")
        inventory = await self.client.get(
            "/api/v1/inventory", headers=self.auth(token)
        )
        self.assertEqual(inventory.status_code, 200)
        self.assertEqual(inventory.json()["data"]["items"][0]["label"], "confirmed milk")

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
