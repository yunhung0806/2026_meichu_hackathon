from datetime import date, timezone
import json
import unittest

from fridge_guardian.adapters.knowledge import FoodKeeperGuide, LemonadeLLM


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return json.dumps(self.payload).encode()


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request, self.timeout = request, timeout
        return FakeResponse(self.payload)


class FoodKeeperGuideTests(unittest.TestCase):
    def setUp(self):
        self.guide = FoodKeeperGuide.bundled()
        self.item = {
            "item_id": "item-1", "label": "apple", "put_at": "2026-09-19T16:01:00+00:00",
            "expires_on": None,
        }

    def test_bundled_snapshot_and_chinese_alias(self):
        self.assertEqual(self.guide.metadata["license"], "CC0-1.0")
        self.assertEqual(self.guide.lookup("有機菠菜")["name_en"], "Spinach")
        self.assertIsNone(self.guide.lookup("unknown prepared dish"))

    def test_dates_and_status_are_derived_without_mutating_expiry(self):
        rows = self.guide.inventory_guidance([self.item], date(2026, 9, 20), timezone.utc)
        self.assertEqual(rows[0].guidance_from, "2026-10-17")
        self.assertEqual(rows[0].guidance_until, "2026-10-31")
        self.assertEqual(rows[0].status, "WITHIN_GUIDANCE")
        self.assertIsNone(self.item["expires_on"])

    def test_package_date_has_priority(self):
        self.item["expires_on"] = "2026-09-30"
        self.assertEqual(self.guide.inventory_guidance([self.item], date(2026, 9, 20)), ())

    def test_question_filters_inventory_passages(self):
        spinach = dict(self.item, item_id="item-2", label="菠菜")
        rows = self.guide.inventory_guidance([self.item, spinach], date(2026, 9, 20))
        passages = self.guide.passages("蘋果要先吃嗎？", rows)
        self.assertEqual(len(passages), 1)
        self.assertIn("apple", passages[0].text)


class LemonadeLLMTests(unittest.TestCase):
    def test_generates_with_openai_compatible_chat_endpoint(self):
        opener = FakeOpener({
            "choices": [{"message": {"role": "assistant", "content": "菠菜先處理。"}}]
        })
        llm = LemonadeLLM("Gemma-3-4b-it-GGUF", timeout=12, opener=opener)
        self.assertEqual(llm.generate("測試問題"), "菠菜先處理。")
        self.assertEqual(opener.request.full_url, "http://127.0.0.1:13305/v1/chat/completions")
        self.assertEqual(opener.timeout, 12)
        payload = json.loads(opener.request.data)
        self.assertEqual(payload["model"], "Gemma-3-4b-it-GGUF")
        self.assertEqual(payload["messages"][0]["content"], "測試問題")
        self.assertEqual(payload["temperature"], 0)

    def test_rejects_non_loopback_server(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            LemonadeLLM("Gemma-3-4b-it-GGUF", "https://example.com/v1")

    def test_rejects_invalid_response(self):
        llm = LemonadeLLM("Gemma-3-4b-it-GGUF", opener=FakeOpener({"choices": []}))
        with self.assertRaisesRegex(ValueError, "no answer"):
            llm.generate("測試問題")


if __name__ == "__main__":
    unittest.main()
