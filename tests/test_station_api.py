from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import httpx

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
        runtime = StationRuntime(self.service, self.capture)
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

    async def test_put_in_requires_user_confirmed_label(self):
        token = await self.identify(self.owner)
        response = await self.client.post(
            "/api/v1/station/operate",
            headers=self.auth(token),
            json={"action": "PUT_IN", "label": "   "},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()
