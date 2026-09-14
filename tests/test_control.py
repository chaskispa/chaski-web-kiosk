from __future__ import annotations

import io
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock

from chaski_web_kiosk.common import MEDIA_DIR
from chaski_web_kiosk.control import Handler


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
        handler.headers["Host"] = "kiosk.local:8080"
        handler.headers["Origin"] = "http://attacker.invalid"
        self.assertFalse(handler.same_origin())

    def test_logo_asset_is_served_with_png_content_type(self) -> None:
        handler = self.handler_with(b"")
        handler.path = "/static/Logo.png"
        handler.send_static = mock.Mock()
        handler.do_GET()
        handler.send_static.assert_called_once_with("Logo.png", "image/png")

    def test_mp4_upload_is_streamed_selected_and_atomically_saved(self) -> None:
        content = b"\x00\x00\x00\x18ftypisom" + b"video-payload"
        handler = self.handler_with(content, "video/mp4")
        handler.headers["X-Filename"] = "unit-upload.mp4"
        handler.send_json = mock.Mock()
        config = {
            "kiosk_name": "TEST",
            "url": "https://example.org",
            "screensaver": {"enabled": True, "timeout_seconds": 300, "media": str(MEDIA_DIR / "old.mp4")},
            "control_port": 8080,
        }
        target = Path(MEDIA_DIR) / "unit-upload.mp4"
        with mock.patch("chaski_web_kiosk.control.load_config", return_value=config), mock.patch(
            "chaski_web_kiosk.control.atomic_write_json"
        ) as write, mock.patch("chaski_web_kiosk.control.send_command"):
            handler.receive_media()
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual(write.call_args.args[1]["screensaver"]["media"], str(target))
        target.unlink()


if __name__ == "__main__":
    unittest.main()
