from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chaski_web_kiosk.common import MEDIA_DIR, atomic_write_json, merge_config, validate_config, validate_media_path, validate_url


class ConfigurationTests(unittest.TestCase):
    def test_complete_valid_configuration(self) -> None:
        config = validate_config(
            {
                "kiosk_name": "SALA-04",
                "url": "https://example.org/signage",
                "screensaver": {
                    "enabled": True,
                    "timeout_seconds": 120,
                    "media": str(MEDIA_DIR / "idle.mp4"),
                },
                "control_port": 8080,
            }
        )
        self.assertEqual(config["kiosk_name"], "SALA-04")
        self.assertEqual(config["screensaver"]["timeout_seconds"], 120)

    def test_merge_preserves_unmentioned_fields(self) -> None:
        original = validate_config({})
        updated = merge_config(original, {"screensaver": {"enabled": False}})
        self.assertFalse(updated["screensaver"]["enabled"])
        self.assertEqual(updated["screensaver"]["timeout_seconds"], 300)
        self.assertEqual(updated["url"], original["url"])

    def test_rejects_malformed_and_dangerous_urls(self) -> None:
        for value in (
            "example.com",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "https://user:pass@example.com",
            "http://example.com:not-a-port/",
            "https://example.com/line\nbreak",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_url(value)

    def test_rejects_media_path_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "inside"):
            merge_config(validate_config({}), {"screensaver": {"media": "../secret.mp4"}})

    def test_accepts_direct_remote_screensaver_url(self) -> None:
        self.assertEqual(
            validate_media_path("https://media.example.org/idle/video?id=42"),
            "https://media.example.org/idle/video?id=42",
        )

    def test_rejects_unknown_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown"):
            merge_config(validate_config({}), {"shell_command": "reboot"})

    def test_atomic_json_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "kiosk.json"
            atomic_write_json(path, {"value": 42})
            self.assertEqual(json.loads(path.read_text()), {"value": 42})
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(list(path.parent.glob(".kiosk.json.*")), [])


if __name__ == "__main__":
    unittest.main()
