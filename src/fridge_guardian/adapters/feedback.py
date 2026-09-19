from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from fridge_guardian.domain import Decision, DecisionCode


class OpenCVFeedback:
    COLORS = {
        DecisionCode.ALLOW_OWNER: (60, 210, 60),
        DecisionCode.ALLOW_SHARED: (60, 210, 60),
        DecisionCode.ITEM_REGISTERED: (60, 210, 60),
        DecisionCode.ITEM_ALREADY_REGISTERED: (60, 210, 210),
        DecisionCode.WARN_NOT_OWNER: (40, 40, 240),
        DecisionCode.NO_FACE: (40, 40, 240),
        DecisionCode.UNKNOWN_USER: (0, 180, 255),
        DecisionCode.AMBIGUOUS_USER: (0, 180, 255),
        DecisionCode.UNKNOWN_ITEM: (0, 180, 255),
    }

    def __init__(
        self,
        debug: bool = False,
        warning_audio_path: str | Path | None = None,
        audio_enabled: bool = True,
    ) -> None:
        self.debug = debug
        self.audio_enabled = audio_enabled
        self.warning_audio_path = (
            Path(warning_audio_path).expanduser().resolve()
            if warning_audio_path
            else None
        )
        self.last_decision: Decision | None = None
        self.last_published_at = 0.0
        self.notice: str | None = None
        self.notice_color = (255, 255, 255)

    def publish(self, decision: Decision) -> None:
        self.last_decision = decision
        self.notice = None
        self.last_published_at = time.monotonic()
        if self.audio_enabled and (
            decision.code is DecisionCode.WARN_NOT_OWNER or decision.warnings
        ):
            self._warning_beep()

    def _warning_beep(self) -> None:
        if self.warning_audio_path and self.warning_audio_path.is_file():
            if os.name == "nt":
                if self._play_windows_audio(self.warning_audio_path):
                    return
            elif self._play_linux_audio(self.warning_audio_path):
                return
        if os.name == "nt":
            try:
                import winsound

                winsound.Beep(880, 220)
                winsound.Beep(660, 260)
                return
            except (OSError, RuntimeError):
                try:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                    return
                except (OSError, RuntimeError):
                    pass
        print("\a", end="", flush=True)

    @staticmethod
    def _play_windows_audio(path: Path) -> bool:
        """Play local audio asynchronously using Windows Media Foundation."""
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if powershell is None:
            return False
        script = r"""
Add-Type -AssemblyName PresentationCore
$player = [System.Windows.Media.MediaPlayer]::new()
$player.Open([Uri]$env:FRIDGE_AUDIO_FILE)
$deadline = [DateTime]::UtcNow.AddSeconds(3)
while (-not $player.NaturalDuration.HasTimeSpan -and [DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 100
}
$player.Volume = 1.0
$player.Play()
if ($player.NaturalDuration.HasTimeSpan) {
    $durationMs = [Math]::Min(8000, [Math]::Max(1500, $player.NaturalDuration.TimeSpan.TotalMilliseconds + 300))
} else {
    $durationMs = 5000
}
Start-Sleep -Milliseconds ([int]$durationMs)
$player.Stop()
$player.Close()
"""
        child_env = os.environ.copy()
        child_env["FRIDGE_AUDIO_FILE"] = str(path)
        try:
            subprocess.Popen(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    script,
                ],
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            return False
        return True

    @staticmethod
    def _play_linux_audio(path: Path) -> bool:
        """Use an already-installed Linux player without changing the OS."""
        players = (
            ("ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"),
            ("mpv", "--no-video", "--really-quiet"),
            ("paplay",),
            ("play", "-q"),
        )
        for executable, *arguments in players:
            player = shutil.which(executable)
            if player is None:
                continue
            try:
                subprocess.Popen(
                    [player, *arguments, str(path)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                continue
            return True
        return False

    def notify(self, message: str, color: tuple[int, int, int] = (255, 255, 255)) -> None:
        self.notice = message
        self.notice_color = color
        self.last_decision = None
        self.last_published_at = time.monotonic()

    def draw(
        self,
        frame,
        transient_message: str | None = None,
        face_boxes: list[tuple[int, int, int, int, float]] | None = None,
    ):
        import cv2

        height, width = frame.shape[:2]
        cv2.rectangle(frame, (0, 0), (width, 92), (25, 25, 25), -1)
        cv2.putText(frame, "U Enroll user   P PUT_IN   T TAKE_OUT   Q Quit", (18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (245, 245, 245), 2)
        cv2.putText(frame, "Keep face clear/front; ONLY the item goes in the green box", (18, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.60, (190, 230, 190), 2)
        if face_boxes is not None:
            if len(face_boxes) == 1:
                face_status = "FACE READY"
                face_color = (60, 220, 60)
            elif len(face_boxes) == 0:
                face_status = "NO FACE"
                face_color = (40, 40, 240)
            else:
                face_status = "ONE FACE ONLY"
                face_color = (0, 180, 255)
            cv2.putText(
                frame,
                face_status,
                (max(18, width - 245), 64),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.68,
                face_color,
                2,
            )
            for x, y, box_width, box_height, _score in face_boxes:
                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + box_width, y + box_height),
                    face_color,
                    2,
                )
        from fridge_guardian.adapters.item import SpatialHistogramItemRecognizer

        x1, y1, x2, y2 = SpatialHistogramItemRecognizer.roi_pixels(frame)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (50, 230, 80), 3)
        cv2.putText(frame, "HANDHELD ITEM", (x1, max(115, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (50, 230, 80), 2)
        message = transient_message
        color = (255, 255, 255)
        if message is None and time.monotonic() - self.last_published_at < 8.0:
            if self.last_decision:
                message = f"{self.last_decision.code.value}: {self.last_decision.message}"
                color = (40, 40, 240) if self.last_decision.warnings else self.COLORS[self.last_decision.code]
            elif self.notice:
                message = self.notice
                color = self.notice_color
        if message:
            cv2.rectangle(frame, (0, height - 64), (width, height), (20, 20, 20), -1)
            cv2.putText(frame, message[:100], (18, height - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
        if self.debug and self.last_decision:
            details = (
                f"face={self.last_decision.identity_confidence:.3f} "
                f"second={self.last_decision.identity_second_score:.3f} "
                f"margin={self.last_decision.identity_margin:.3f} "
                f"valid={self.last_decision.identity_valid_frames} "
                f"votes={self.last_decision.identity_vote_ratio:.2f}"
            )
            cv2.putText(
                frame,
                details,
                (18, height - 78),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (220, 220, 220),
                1,
            )
        return frame
