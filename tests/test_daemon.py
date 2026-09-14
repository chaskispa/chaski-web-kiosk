from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chaski_web_kiosk.daemon import KioskDaemon, KioskState


class DaemonTests(unittest.TestCase):
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
        with mock.patch.object(daemon, "idle_milliseconds", return_value=0), mock.patch.object(
            daemon, "stop_screensaver"
        ) as stop, mock.patch("chaski_web_kiosk.daemon.time.monotonic", return_value=2):
            daemon.check_screensaver()
        stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
