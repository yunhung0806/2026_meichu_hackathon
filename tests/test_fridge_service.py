from datetime import date, datetime, timezone, timedelta

from fridge_guardian.application.fridge_service import FridgeService, PutOptions
from fridge_guardian.adapters.knowledge import FoodKeeperGuide, LocalKnowledge, FoodQuestions
from fridge_guardian.domain import Action, DecisionCode
import unittest
import tests.test_flow as flow_tests
from tests.test_flow import frames


class FridgeServiceTests(unittest.TestCase):
    def setUp(self):
        flow_tests.MockFlowTests.setUp(self)
        self.now = datetime(2026, 9, 19, 16, 1, tzinfo=timezone.utc)
        self.service = FridgeService(self.coordinator, clock=lambda: self.now)
        self.owner = self.repo.add_user("Owner")
        self.other = self.repo.add_user("Other")
        self.identity.user_id = self.owner.user_id
        self.login = self.service.identify(frames())

    def tearDown(self):
        flow_tests.MockFlowTests.tearDown(self)

    def put(self, shared=False, expires=None):
        result = self.service.process(self.login.token, Action.PUT_IN, frames(), PutOptions("apple", shared, expires))
        self.items.item_id = result.decision.item_id
        return result

    def test_inventory_expiry_and_take_out(self):
        self.assertEqual(self.login.display_name, "Owner")
        self.put(expires=date(2026, 9, 19))
        inventory = self.service.inventory(self.login.token)
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["owner_name"], "Owner")
        result = self.service.process(self.login.token, Action.TAKE_OUT, frames())
        self.assertEqual(result.decision.code, DecisionCode.ALLOW_OWNER)
        self.assertTrue(result.warnings)  # Taipei is already September 20
        self.assertEqual(self.service.inventory(self.login.token), [])

    def test_private_denied_shared_allowed(self):
        self.put()
        self.identity.user_id = self.other.user_id
        other = self.service.identify(frames())
        denied = self.service.process(other.token, Action.TAKE_OUT, frames())
        self.assertEqual(denied.decision.code, DecisionCode.WARN_NOT_OWNER)
        self.assertEqual(len(self.service.inventory(self.login.token)), 1)
        self.assertEqual(self.service.inventory(other.token), [])
        denied_put = self.service.process(other.token, Action.PUT_IN, frames(), PutOptions("stolen", True))
        self.assertEqual(denied_put.decision.code, DecisionCode.WARN_NOT_OWNER)
        self.identity.user_id = self.owner.user_id
        self.service.process(self.login.token, Action.TAKE_OUT, frames())
        self.put(shared=True)
        self.identity.user_id = self.other.user_id
        allowed = self.service.process(other.token, Action.TAKE_OUT, frames())
        self.assertEqual(allowed.decision.code, DecisionCode.ALLOW_SHARED)

    def test_changed_face_and_expired_login(self):
        self.identity.user_id = self.other.user_id
        with self.assertRaises(PermissionError):
            self.service.process(self.login.token, Action.PUT_IN, frames(), PutOptions("apple"))
        self.now += timedelta(minutes=6)
        with self.assertRaises(PermissionError):
            self.service.inventory(self.login.token)

    def test_reminders_persist_are_private_and_idempotent(self):
        self.put(expires=date(2026, 9, 21))
        notice = self.service.reminders(self.login.token)[0]
        self.service.refresh_reminders()
        self.assertEqual(len(self.service.reminders(self.login.token)), 1)
        restarted = FridgeService(self.coordinator, clock=lambda: self.now)
        login = restarted.identify(frames())
        self.assertEqual(len(restarted.reminders(login.token)), 1)
        self.identity.user_id = self.other.user_id
        other = restarted.identify(frames())
        restarted.acknowledge_reminder(other.token, notice["item_id"], notice["expires_on"])
        self.assertEqual(restarted.reminders(other.token), [])
        self.assertEqual(len(restarted.reminders(login.token)), 1)
        restarted.acknowledge_reminder(login.token, notice["item_id"], notice["expires_on"])
        self.assertEqual(restarted.reminders(login.token), [])

    def test_optional_date_and_rag_missing_resources(self):
        self.put()
        self.assertIsNone(self.service.inventory(self.login.token)[0]["expires_on"])
        self.assertEqual(self.service.reminders(self.login.token), [])
        knowledge = LocalKnowledge(self.tempdir.name)
        questions = FoodQuestions(self.service, knowledge)
        result = questions.ask(self.login.token, "如何保存蘋果")
        self.assertEqual(result.status, "LLM_NOT_CONFIGURED")
        self.assertTrue(result.passages[0].source.startswith("foodkeeper/"))
        folder = knowledge.directory / "storage"
        folder.mkdir()
        (folder / "test.md").write_text("蘋果保存：這是測試資料。", encoding="utf-8")
        result = questions.ask(self.login.token, "如何保存蘋果")
        self.assertEqual(result.status, "LLM_NOT_CONFIGURED")
        self.assertTrue(result.passages)
        class FakeLLM:
            def generate(self, prompt):
                assert "storage/test.md" in prompt
                return "測試回答"
        questions.llm = FakeLLM()
        self.assertEqual(questions.ask(self.login.token, "如何保存蘋果").status, "OK")

    def test_foodkeeper_guidance_is_not_an_expiry_date(self):
        self.put()
        questions = FoodQuestions(self.service, LocalKnowledge(self.tempdir.name))
        guidance = questions.storage_guidance(self.login.token)
        self.assertEqual(guidance[0]["food_name"], "蘋果")
        self.assertEqual(guidance[0]["status"], "WITHIN_GUIDANCE")
        self.assertEqual(guidance[0]["guidance_from"], "2026-10-18")
        self.assertIsNone(self.service.inventory(self.login.token)[0]["expires_on"])

    def test_package_expiry_takes_priority_over_foodkeeper(self):
        self.put(expires=date(2026, 10, 1))
        questions = FoodQuestions(self.service, LocalKnowledge(self.tempdir.name))
        self.assertEqual(questions.storage_guidance(self.login.token), ())
        answer = questions.ask(self.login.token, "哪些食物要先處理？")
        self.assertEqual(answer.status, "LLM_NOT_CONFIGURED")
        self.assertTrue(answer.passages[0].source.startswith("inventory/package-date/"))

    def test_foodkeeper_alias_and_status(self):
        guide = FoodKeeperGuide.bundled()
        self.assertEqual(guide.lookup("有機菠菜")["name_en"], "Spinach")
        self.put()
        rows = FoodQuestions(self.service, LocalKnowledge(self.tempdir.name)).storage_guidance(self.login.token)
        self.assertEqual(rows[0]["status"], "WITHIN_GUIDANCE")
