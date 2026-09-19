from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
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

    def test_disabled_system_audio_does_not_beep(self):
        feedback = OpenCVFeedback(audio_enabled=False)
        decision = Decision(
            session_id="session",
            action=Action.TAKE_OUT,
            code=DecisionCode.WARN_NOT_OWNER,
            message="warning",
        )
        with patch.object(feedback, "_warning_beep") as beep:
            feedback.publish(decision)
        beep.assert_not_called()

    def test_configured_local_audio_is_preferred(self):
        path = Path(__file__)
        feedback = OpenCVFeedback(warning_audio_path=path)
        with (
            patch("fridge_guardian.adapters.feedback.os.name", "nt"),
            patch.object(feedback, "_play_windows_audio", return_value=True) as play,
        ):
            feedback._warning_beep()
        play.assert_called_once_with(path.resolve())

    def test_windows_audio_starts_hidden_player_with_path_in_environment(self):
        path = Path(__file__).resolve()
        with (
            patch(
                "fridge_guardian.adapters.feedback.shutil.which",
                return_value=r"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
            ),
            patch("fridge_guardian.adapters.feedback.subprocess.Popen") as popen,
        ):
            started = OpenCVFeedback._play_windows_audio(path)

        self.assertTrue(started)
        kwargs = popen.call_args.kwargs
        self.assertEqual(kwargs["env"]["FRIDGE_AUDIO_FILE"], str(path))
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)

    def test_linux_audio_uses_an_installed_player(self):
        path = Path(__file__).resolve()
        with (
            patch(
                "fridge_guardian.adapters.feedback.shutil.which",
                side_effect=lambda name: "/usr/bin/ffplay" if name == "ffplay" else None,
            ),
            patch("fridge_guardian.adapters.feedback.subprocess.Popen") as popen,
        ):
            started = OpenCVFeedback._play_linux_audio(path)

        self.assertTrue(started)
        self.assertEqual(popen.call_args.args[0][-1], str(path))
        self.assertTrue(popen.call_args.kwargs["start_new_session"])


if __name__ == "__main__":
    unittest.main()
