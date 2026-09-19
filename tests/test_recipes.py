import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fridge_guardian.application.fridge_service import FridgeService, PutOptions
from fridge_guardian.application.recipes import RecipeQuestions
from fridge_guardian.domain import Action
from tests.test_flow import MockFlowTests, frames


class RecipeTests(unittest.TestCase):
    def setUp(self):
        MockFlowTests.setUp(self)
        self.now = datetime(2026, 9, 19, 16, 1, tzinfo=timezone.utc)
        self.service = FridgeService(self.coordinator, clock=lambda: self.now)
        self.owner = self.repo.add_user("Owner")
        self.identity.user_id = self.owner.user_id
        self.login = self.service.identify(frames())
        self.recipes = RecipeQuestions(self.service, Path(__file__).resolve().parents[1] / "data/knowledge/recipes")

    def tearDown(self):
        MockFlowTests.tearDown(self)

    def put(self, label, expires=None):
        self.items.item_id = None
        return self.service.process(self.login.token, Action.PUT_IN, frames(), PutOptions(label, False, expires)).decision.item_id

    def test_urgent_rank_missing_and_expired_exclusion(self):
        self.put("番茄", date(2026, 9, 20))  # Today in Taipei, despite UTC being the 19th.
        self.put("雞蛋", date(2026, 9, 25))
        self.put("高麗菜", date(2026, 9, 24))
        self.put("菠菜", date(2026, 9, 19))
        result = self.recipes.recommend(self.login.token)
        self.assertEqual(result["status"], "RETRIEVAL_ONLY")
        self.assertEqual(result["ingredients"][0]["label"], "番茄")
        self.assertEqual(result["ingredients"][0]["days_left"], 0)
        top = result["recipes"][0]
        self.assertEqual(top["id"], "tomato-eggs")
        self.assertEqual(top["missing"], [])
        self.assertEqual(top["use_first"], ["番茄"])
        self.assertIn("菠菜", next(r for r in result["recipes"] if r["id"] == "spinach-eggs")["missing"])
        self.assertEqual(result["excluded"][0]["label"], "菠菜")
        self.assertEqual(len(self.service.inventory(self.login.token)), 4)

    def test_guidance_unknown_and_removed(self):
        spinach = self.put("菠菜")
        cabbage = self.put("高麗菜")
        with self.repo.connection:
            self.repo.connection.execute("UPDATE inventory SET put_at=? WHERE item_id=?", ((self.now - timedelta(days=8)).isoformat(), spinach))
        result = self.recipes.recommend(self.login.token)
        self.assertEqual(result["excluded"][0]["status"], "BEYOND_GUIDANCE")
        self.assertEqual(result["ingredients"][0]["status"], "UNKNOWN_DATE")
        self.items.item_id = cabbage
        self.service.process(self.login.token, Action.TAKE_OUT, frames())
        self.assertEqual(self.recipes.recommend(self.login.token)["status"], "NO_MATCH")

    def test_no_substring_matches_or_other_users_inventory(self):
        self.put("蛋糕", date(2026, 9, 21))
        self.assertEqual(self.recipes.recommend(self.login.token)["status"], "NO_MATCH")
        other = self.repo.add_user("Other")
        self.identity.user_id = other.user_id
        login = self.service.identify(frames())
        self.assertEqual(self.recipes.recommend(login.token)["status"], "EMPTY_INVENTORY")
        with self.assertRaises(PermissionError):
            self.recipes.recommend("invalid")

    def test_rag_grounding_and_model_failure_fallback(self):
        self.put("番茄", date(2026, 9, 21))
        self.put("菠菜", date(2026, 9, 19))
        class FakeLLM:
            def generate(inner, prompt):
                context = json.loads(prompt.split("\n", 1)[1])
                self.assertEqual([i["label"] for i in context["ingredients"]], ["番茄"])
                self.assertEqual(context["recipes"][0]["missing"], ["雞蛋"])
                self.assertIn("source", context["recipes"][0])
                return "可做番茄炒蛋，還缺雞蛋。"
        self.recipes.llm = FakeLLM()
        self.assertEqual(self.recipes.recommend(self.login.token)["status"], "OK")
        class FailedLLM:
            def generate(self, prompt):
                raise OSError("offline")
        self.recipes.llm = FailedLLM()
        result = self.recipes.recommend(self.login.token)
        self.assertEqual(result["status"], "LLM_UNAVAILABLE")
        self.assertTrue(result["recipes"])

    def test_missing_recipe_directory(self):
        self.put("番茄")
        self.recipes.directory = Path(self.tempdir.name) / "missing"
        self.assertEqual(self.recipes.recommend(self.login.token)["status"], "NO_MATCH")


if __name__ == "__main__":
    unittest.main()
