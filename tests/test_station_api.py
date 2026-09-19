from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import httpx

from fridge_guardian.adapters.knowledge import FoodQuestions, LocalKnowledge
from fridge_guardian.adapters.sqlite_repository import SQLiteRepository
from fridge_guardian.application import SessionCoordinator
from fridge_guardian.application.fridge_service import FridgeService
from fridge_guardian.domain import FrameSample, utc_now
from fridge_guardian.station_api import StationRuntime, create_app
from tests.fakes import FakeIdentityProvider, FakeItemRecognizer, RecordingFeedback


class FakeCameraCapture:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        session_id = str(uuid4())
        return [FrameSample(session_id, utc_now(), object()) for _ in range(3)]


class FakeLLM:
    def __init__(self):
        self.prompt = ""

    def generate(self, prompt):
        self.prompt = prompt
        return "建議先處理菠菜，蘋果較耐放。"


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
        questions = FoodQuestions(self.service, LocalKnowledge(self.tempdir.name))
        runtime = StationRuntime(self.service, self.capture, questions=questions)
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
        self.items.item_id = None
        response = await self.client.post(
            "/api/v1/station/operate",
            headers=self.auth(token),
            json={"action": "PUT_IN", "label": label, "shared": shared},
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["decision"], "ITEM_REGISTERED")
        self.items.item_id = data["item_id"]
        return data

    async def test_health_and_successful_identification(self):
        response = await self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["camera_owner"], "python")
        token = await self.identify(self.owner)
        self.assertTrue(token)
        self.assertEqual(self.capture.calls, 1)

    async def test_put_in_inventory_and_owner_take_out(self):
        token = await self.identify(self.owner)
        stored = await self.put(token, label="confirmed milk")
        inventory = await self.client.get(
            "/api/v1/inventory", headers=self.auth(token)
        )
        self.assertEqual(inventory.status_code, 200)
        self.assertEqual(inventory.json()["data"]["items"][0]["label"], "confirmed milk")

        self.identity.user_id = self.owner.user_id
        response = await self.client.post(
            "/api/v1/station/operate",
            headers=self.auth(token),
            json={"action": "TAKE_OUT"},
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

    async def test_non_owner_warning_keeps_inventory(self):
        owner_token = await self.identify(self.owner)
        stored = await self.put(owner_token)
        other_token = await self.identify(self.other)

        self.identity.user_id = self.other.user_id
        response = await self.client.post(
            "/api/v1/station/operate",
            headers=self.auth(other_token),
            json={"action": "TAKE_OUT"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["outcome"], "WARNING")
        self.assertEqual(data["decision"], "WARN_NOT_OWNER")
        self.assertEqual(data["item_id"], stored["item_id"])
        owner_inventory = (await self.client.get(
            "/api/v1/inventory", headers=self.auth(owner_token)
        )).json()["data"]["items"]
        self.assertEqual(len(owner_inventory), 1)

    async def test_unknown_item_and_invalid_token(self):
        token = await self.identify(self.owner)
        self.items.item_id = None
        self.identity.user_id = self.owner.user_id
        response = await self.client.post(
            "/api/v1/station/operate",
            headers=self.auth(token),
            json={"action": "TAKE_OUT"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["outcome"], "UNKNOWN")
        self.assertEqual(response.json()["data"]["decision"], "UNKNOWN_ITEM")

        invalid = await self.client.get(
            "/api/v1/inventory", headers=self.auth("not-a-token")
        )
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(invalid.json()["error"]["code"], "UNAUTHORIZED")
        invalid_operation = await self.client.post(
            "/api/v1/station/operate",
            headers=self.auth("not-a-token"),
            json={"action": "TAKE_OUT"},
        )
        self.assertEqual(invalid_operation.status_code, 401)
        self.assertEqual(invalid_operation.json()["error"]["code"], "UNAUTHORIZED")
        self.assertEqual(self.capture.calls, 2)  # identify + unknown item; invalid token captures nothing

    async def test_history_records_actor_owner_and_enforces_privacy(self):
        owner_token = await self.identify(self.owner)
        stored = await self.put(owner_token, label="milk", shared=True)
        other_token = await self.identify(self.other)
        await self.client.post("/api/v1/station/operate", headers=self.auth(other_token),
                               json={"action": "TAKE_OUT"})
        camera_calls = self.capture.calls
        for token in (owner_token, other_token):
            response = await self.client.get("/api/v1/history", headers=self.auth(token))
            self.assertEqual(response.status_code, 200)
            events = response.json()["data"]["events"]
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(event["taker_name"], "Other")
            self.assertEqual(event["owner_name"], "Owner")
            self.assertEqual(event["label"], "milk")
            self.assertEqual(event["item_id"], stored["item_id"])
            self.assertEqual(event["decision"], "ALLOW_SHARED")
        self.assertEqual(self.capture.calls, camera_calls)
        unrelated = await self.identify(self.repo.add_user("Unrelated"))
        response = await self.client.get("/api/v1/history", headers=self.auth(unrelated))
        self.assertEqual(response.json()["data"]["events"], [])
        for headers in ({}, self.auth("invalid")):
            self.assertEqual((await self.client.get("/api/v1/history", headers=headers)).status_code, 401)
        from datetime import timedelta
        self.service.clock = lambda: utc_now() + timedelta(minutes=6)
        self.assertEqual((await self.client.get("/api/v1/history", headers=self.auth(owner_token))).status_code, 401)

    async def test_history_warning_unknown_and_restart(self):
        owner_token = await self.identify(self.owner)
        await self.put(owner_token)
        other_token = await self.identify(self.other)
        await self.client.post("/api/v1/station/operate", headers=self.auth(other_token),
                               json={"action": "TAKE_OUT"})
        self.items.item_id = None
        await self.client.post("/api/v1/station/operate", headers=self.auth(other_token),
                               json={"action": "TAKE_OUT"})
        response = await self.client.get("/api/v1/history", headers=self.auth(other_token))
        events = response.json()["data"]["events"]
        self.assertEqual([e["decision"] for e in events], ["UNKNOWN_ITEM", "WARN_NOT_OWNER"])
        self.assertIsNone(events[0]["owner_id"])
        self.assertEqual(len(self.service.inventory(owner_token)), 1)
        # Reopen SQLite and create a fresh service: history survives, tokens do not.
        with SQLiteRepository(self.repo.path) as reopened:
            service = FridgeService(SessionCoordinator(reopened, self.identity, self.items, RecordingFeedback()))
            with self.assertRaises(PermissionError):
                service.history(other_token)
            login = service.identify(self.capture())
            self.assertEqual(service.history(login.token), events)

    async def test_recipe_api_uses_inventory_without_camera_capture(self):
        token = await self.identify(self.owner)
        await self.put(token, label="番茄")
        calls = self.capture.calls
        response = await self.client.post("/api/v1/recipes/recommend", headers=self.auth(token), json={})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["recipes"][0]["title"], "番茄炒蛋")
        self.assertEqual(data["recipes"][0]["missing"], ["雞蛋"])
        self.assertEqual(self.capture.calls, calls)
        unauthorized = await self.client.post("/api/v1/recipes/recommend", json={})
        self.assertEqual(unauthorized.status_code, 401)
        invalid = await self.client.post("/api/v1/recipes/recommend", headers=self.auth(token), json={"question": "  "})
        self.assertEqual(invalid.status_code, 422)

    async def test_put_in_requires_user_confirmed_label(self):
        token = await self.identify(self.owner)
        response = await self.client.post(
            "/api/v1/station/operate",
            headers=self.auth(token),
            json={"action": "PUT_IN", "label": "   "},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

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
