from __future__ import annotations

import unittest
from unittest.mock import patch

from fridge_guardian.adapters.feedback import OpenCVFeedback
from fridge_guardian.domain import Action, Decision, DecisionCode


class FeedbackTests(unittest.TestCase):
    def test_expired_owner_take_out_triggers_warning_beep(self):
        feedback = OpenCVFeedback()
        decision = Decision("expiry", Action.TAKE_OUT, DecisionCode.ALLOW_OWNER,
                            "expired", warnings=("EXPIRED",))
        with patch.object(feedback, "_warning_beep") as beep:
            feedback.publish(decision)
        beep.assert_called_once_with()

    def test_warn_not_owner_triggers_warning_beep(self):
        feedback = OpenCVFeedback()
        decision = Decision(
            session_id="session",
            action=Action.TAKE_OUT,
            code=DecisionCode.WARN_NOT_OWNER,
            message="warning",
        )
        with patch.object(feedback, "_warning_beep") as beep:
            feedback.publish(decision)
        beep.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
