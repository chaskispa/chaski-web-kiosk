from __future__ import annotations

import io
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock

from chaski_player.common import MEDIA_DIR
from chaski_player.control import Handler


class ControlAPITests(unittest.TestCase):
    @staticmethod
    def handler_with(body: bytes, content_type: str = "application/json") -> Handler:
        handler = object.__new__(Handler)
        headers = Message()
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(body))
        handler.headers = headers
        handler.rfile = io.BytesIO(body)
        return handler

    def test_json_body_is_bounded(self) -> None:
        handler = self.handler_with(b"{}")
        self.assertEqual(handler.read_body(), {})
        handler.headers.replace_header("Content-Length", "999999")
        with self.assertRaisesRegex(ValueError, "Request body"):
            handler.read_body()

    def test_non_json_body_is_rejected(self) -> None:
        handler = self.handler_with(b"url=x", "application/x-www-form-urlencoded")
        with self.assertRaisesRegex(ValueError, "application/json"):
            handler.read_body()

    def test_cross_origin_browser_request_is_rejected(self) -> None:
        handler = self.handler_with(b"{}")
        handler.headers["Host"] = "player.local:8080"
        handler.headers["Origin"] = "http://attacker.invalid"
        self.assertFalse(handler.same_origin())

    def test_mp4_upload_is_streamed_selected_and_atomically_saved(self) -> None:
        content = b"\x00\x00\x00\x18ftypisom" + b"video-payload"
        handler = self.handler_with(content, "video/mp4")
        handler.headers["X-Filename"] = "unit-upload.mp4"
        handler.send_json = mock.Mock()
        config = {
            "player_name": "TEST",
            "url": "https://example.org",
            "screensaver": {"enabled": True, "timeout_seconds": 300, "media": str(MEDIA_DIR / "old.mp4")},
            "control_port": 8080,
        }
        target = Path(MEDIA_DIR) / "unit-upload.mp4"
        with mock.patch("chaski_player.control.load_config", return_value=config), mock.patch(
            "chaski_player.control.atomic_write_json"
        ) as write, mock.patch("chaski_player.control.send_command"):
            handler.receive_media()
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual(write.call_args.args[1]["screensaver"]["media"], str(target))
        target.unlink()


if __name__ == "__main__":
    unittest.main()
