from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

from chaski_web_kiosk.daemon import KioskDaemon, KioskState


class DaemonTests(unittest.TestCase):
    def test_chromium_translation_ui_is_disabled(self) -> None:
        daemon = KioskDaemon()
        daemon.config = {"url": "https://example.org"}
        with mock.patch.object(daemon, "executable", return_value="/usr/bin/chromium"):
            command = daemon.chromium_command()
        self.assertIn("--disable-translate", command)
        feature_flag = next(item for item in command if item.startswith("--disable-features="))
        self.assertIn("Translate", feature_flag)
        self.assertIn("TranslateUI", feature_flag)

    def test_managed_policy_disables_translation(self) -> None:
        policy_path = Path(__file__).resolve().parents[1] / "config" / "chromium-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertIs(policy["TranslateEnabled"], False)

    def test_start_is_idempotent_while_chromium_runs(self) -> None:
        daemon = KioskDaemon()
        daemon.config = {"url": "https://example.org"}
        process = mock.Mock()
        process.poll.return_value = None
        process.pid = 123
        with mock.patch.object(daemon, "display_ready", return_value=True), mock.patch.object(
            daemon, "chromium_command", return_value=["chromium"]
        ), mock.patch("chaski_web_kiosk.daemon.subprocess.Popen", return_value=process) as popen:
            daemon.start_chromium()
            daemon.start_chromium()
        popen.assert_called_once()
        self.assertEqual(daemon.state, KioskState.WEB_CONTENT)

    def test_missing_screensaver_never_spawns_mpv(self) -> None:
        daemon = KioskDaemon()
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.mp4"
            daemon.config = {"screensaver": {"enabled": True, "media": str(missing)}}
            with mock.patch("chaski_web_kiosk.daemon.subprocess.Popen") as popen:
                self.assertFalse(daemon.start_screensaver())
            popen.assert_not_called()

    def test_mpv_uses_continuous_gapless_looping(self) -> None:
        daemon = KioskDaemon()
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "loop.mp4"
            media.write_bytes(b"video")
            daemon.config = {"screensaver": {"enabled": True, "media": str(media)}}
            process = mock.Mock()
            with mock.patch.object(daemon, "executable", side_effect=lambda name: f"/usr/bin/{name}"), mock.patch.object(
                daemon, "reload_content_behind_screensaver"
            ) as refresh, mock.patch("chaski_web_kiosk.daemon.subprocess.Popen", return_value=process) as popen:
                self.assertTrue(daemon.start_screensaver())
            refresh.assert_called_once_with()
        command = popen.call_args.args[0]
        self.assertIn("--loop-file=inf", command)
        self.assertIn("--loop-playlist=inf", command)
        self.assertIn("--gapless-audio=yes", command)

    def test_unexpected_chromium_exit_enters_error_with_backoff(self) -> None:
        daemon = KioskDaemon()
        daemon.state = KioskState.WEB_CONTENT
        process = mock.Mock()
        process.poll.return_value = 9
        daemon.chromium = process
        daemon.chromium_started_at = 0
        with mock.patch("chaski_web_kiosk.daemon.time.monotonic", return_value=10):
            daemon.check_chromium()
        self.assertIsNone(daemon.chromium)
        self.assertEqual(daemon.state, KioskState.ERROR)
        self.assertGreater(daemon.chromium_next_start, 10)

    def test_any_x_input_stops_running_screensaver(self) -> None:
        daemon = KioskDaemon()
        daemon.mpv = mock.Mock()
        daemon.mpv_started_at = 1
        daemon.input_fds = {99: Path("/dev/input/event-test")}
        with mock.patch.object(daemon, "physical_input_detected", return_value=True), mock.patch.object(
            daemon, "idle_milliseconds", return_value=0
        ), mock.patch.object(
            daemon, "stop_screensaver"
        ) as stop, mock.patch("chaski_web_kiosk.daemon.time.monotonic", return_value=2):
            daemon.check_screensaver()
        stop.assert_called_once_with()

    def test_x_idle_reset_during_video_is_not_treated_as_physical_input(self) -> None:
        daemon = KioskDaemon()
        process = mock.Mock()
        process.poll.return_value = None
        daemon.mpv = process
        daemon.mpv_started_at = 1
        daemon.last_idle_ms = 30_000
        daemon.input_fds = {99: Path("/dev/input/event-test")}
        with mock.patch.object(daemon, "physical_input_detected", return_value=False), mock.patch.object(
            daemon, "idle_milliseconds", return_value=0
        ), mock.patch.object(daemon, "stop_screensaver") as stop:
            daemon.check_screensaver()
        stop.assert_not_called()
        self.assertIs(daemon.mpv, process)

    def test_screensaver_starts_at_configured_30_second_idle_timeout(self) -> None:
        daemon = KioskDaemon()
        daemon.config = {"screensaver": {"enabled": True, "timeout_seconds": 30}}
        daemon.last_idle_ms = 29_000
        with mock.patch.object(daemon, "idle_milliseconds", return_value=30_000), mock.patch.object(
            daemon, "start_screensaver"
        ) as start:
            daemon.check_screensaver()
        start.assert_called_once_with()

    def test_chromium_restart_without_video_does_not_disable_screensaver(self) -> None:
        daemon = KioskDaemon()
        daemon.saver_dismissed = False
        daemon.stop_chromium()
        self.assertFalse(daemon.saver_dismissed)

    def test_wake_reveals_already_refreshed_chromium(self) -> None:
        daemon = KioskDaemon()
        daemon.mpv = mock.Mock()
        daemon.chromium = mock.Mock()
        daemon.chromium.poll.return_value = None
        daemon.chromium.pid = 123
        chromium = daemon.chromium
        with mock.patch.object(daemon, "terminate") as terminate:
            daemon.stop_screensaver()
        terminate.assert_called_once()
        self.assertIsNone(daemon.mpv)
        self.assertIs(daemon.chromium, chromium)
        self.assertEqual(daemon.state, KioskState.WEB_CONTENT)

    def test_clean_video_exit_without_input_restarts_screensaver(self) -> None:
        daemon = KioskDaemon()
        process = mock.Mock()
        process.poll.return_value = 0
        daemon.mpv = process
        daemon.mpv_started_at = 1
        daemon.last_idle_ms = 30_000
        with mock.patch.object(daemon, "idle_milliseconds", return_value=31_000), mock.patch.object(
            daemon, "start_screensaver", return_value=True
        ) as start:
            daemon.check_screensaver()
        start.assert_called_once_with(manual=True, refresh_content=False)
        self.assertFalse(daemon.saver_dismissed)


if __name__ == "__main__":
    unittest.main()
