import unittest

from fridge_guardian.application.policy import ownership_decision
from fridge_guardian.domain import DecisionCode, Item


class OwnershipPolicyTests(unittest.TestCase):
    def test_owner_is_allowed(self):
        item = Item("item-1", "user-a", "milk")
        self.assertEqual(
            ownership_decision(item, "user-a", shared=False),
            DecisionCode.ALLOW_OWNER,
        )

    def test_shared_user_is_allowed(self):
        item = Item("item-1", "user-a", "milk")
        self.assertEqual(
            ownership_decision(item, "user-b", shared=True),
            DecisionCode.ALLOW_SHARED,
        )

    def test_non_owner_is_warned(self):
        item = Item("item-1", "user-a", "milk")
        self.assertEqual(
            ownership_decision(item, "user-b", shared=False),
            DecisionCode.WARN_NOT_OWNER,
        )


if __name__ == "__main__":
    unittest.main()
