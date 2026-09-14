"""Playback supervisor and explicit state machine for CHASKI Web Kiosk."""

from __future__ import annotations

import http.client
import json
import logging
import os
import queue
import shutil
import signal
import socket
import socketserver
import subprocess
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .common import COMMAND_SOCKET, CONFIG_FILE, ROOT, RUNTIME, STATUS_FILE, atomic_write_json, is_remote_media, load_config


LOG = logging.getLogger("chaski-web-kiosk")


class KioskState(str, Enum):
    BOOT = "BOOT"
    STARTING = "STARTING"
    WEB_CONTENT = "WEB_CONTENT"
    SCREENSAVER = "SCREENSAVER"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"


class CommandHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            request = json.loads(self.rfile.readline(65536).decode("utf-8"))
            command = request.get("command")
            if command not in {"reload", "restart-playback", "screensaver-start", "screensaver-stop"}:
                raise ValueError("Unknown command")
            self.server.command_queue.put(command)  # type: ignore[attr-defined]
            response = {"ok": True, "queued": command}
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            response = {"ok": False, "error": str(exc)}
        self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")


class CommandServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path: str, command_queue: queue.Queue[str]):
        self.command_queue = command_queue
        super().__init__(path, CommandHandler)


class KioskDaemon:
    def __init__(self) -> None:
        self.state = KioskState.BOOT
        self.state_detail = "daemon starting"
        self.config: dict[str, Any] = {}
        self.config_mtime_ns = 0
        self.chromium: subprocess.Popen[bytes] | None = None
        self.mpv: subprocess.Popen[bytes] | None = None
        self.stop_event = threading.Event()
        self.commands: queue.Queue[str] = queue.Queue()
        self.command_server: CommandServer | None = None
        self.chromium_started_at = 0.0
        self.chromium_failures = 0
        self.chromium_next_start = 0.0
        self.devtools_failures = 0
        self.last_devtools_check = 0.0
        self.last_network_check = 0.0
        self.network_failures = 0
        self.mpv_next_start = 0.0
        self.mpv_started_at = 0.0
        self.saver_dismissed = False
        self.last_status: dict[str, Any] | None = None
        self.last_status_write = 0.0
        self.last_watchdog = 0.0

    def transition(self, state: KioskState, detail: str) -> None:
        if state != self.state or detail != self.state_detail:
            LOG.info("state %s -> %s: %s", self.state.value, state.value, detail)
            self.state = state
            self.state_detail = detail
            self.write_status(force=True)

    def load_configuration(self, *, initial: bool = False) -> bool:
        try:
            new_config = load_config()
            mtime = CONFIG_FILE.stat().st_mtime_ns
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            try:
                self.config_mtime_ns = CONFIG_FILE.stat().st_mtime_ns
            except OSError:
                pass
            LOG.error("cannot load configuration: %s", exc)
            self.transition(KioskState.ERROR, f"invalid configuration: {exc}")
            return False
        changed = new_config != self.config
        self.config = new_config
        self.config_mtime_ns = mtime
        if changed and not initial:
            LOG.info("configuration changed; restarting web playback")
            self.stop_chromium()
        return True

    @staticmethod
    def executable(*names: str) -> str:
        for name in names:
            found = shutil.which(name)
            if found:
                return found
        raise FileNotFoundError(f"required executable not found: {', '.join(names)}")

    def display_ready(self) -> bool:
        try:
            return subprocess.run(
                [self.executable("xdpyinfo"), "-display", os.environ.get("DISPLAY", ":0")],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            ).returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def chromium_command(self) -> list[str]:
        binary = self.executable("chromium", "chromium-browser")
        cache = RUNTIME / "chromium-cache"
        cache.mkdir(parents=True, exist_ok=True)
        profile = Path("/var/lib/chaski-web-kiosk/chromium")
        profile.mkdir(parents=True, exist_ok=True)
        return [
            binary,
            "--kiosk",
            "--no-first-run",
            "--no-default-browser-check",
            "--noerrdialogs",
            "--disable-session-crashed-bubble",
            "--disable-component-update",
            "--disable-features=Translate,MediaRouter,AutofillServerCommunication",
            "--disable-pinch",
            "--overscroll-history-navigation=0",
            "--autoplay-policy=no-user-gesture-required",
            "--password-store=basic",
            "--ozone-platform=x11",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=9222",
            f"--user-data-dir={profile}",
            f"--disk-cache-dir={cache}",
            "--disk-cache-size=67108864",
            self.config["url"],
        ]

    def start_chromium(self) -> None:
        if self.chromium is not None or time.monotonic() < self.chromium_next_start:
            return
        if not self.display_ready():
            self.transition(KioskState.STARTING, "waiting for X display")
            self.chromium_next_start = time.monotonic() + 2
            return
        try:
            self.chromium = subprocess.Popen(self.chromium_command(), start_new_session=True)
        except (OSError, KeyError) as exc:
            self.chromium_failures += 1
            self.chromium_next_start = time.monotonic() + min(30, 2 ** min(self.chromium_failures, 5))
            self.transition(KioskState.ERROR, f"could not start Chromium: {exc}")
            return
        self.chromium_started_at = time.monotonic()
        self.devtools_failures = 0
        self.transition(KioskState.WEB_CONTENT, "Chromium running")

    @staticmethod
    def terminate(process: subprocess.Popen[bytes] | None, name: str) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                LOG.error("could not terminate %s process group", name)

    def stop_chromium(self) -> None:
        self.stop_screensaver()
        self.terminate(self.chromium, "Chromium")
        self.chromium = None
        self.chromium_next_start = time.monotonic() + 0.5

    def check_chromium(self) -> None:
        if self.chromium is None:
            self.start_chromium()
            return
        code = self.chromium.poll()
        if code is not None:
            ran_for = time.monotonic() - self.chromium_started_at
            self.chromium = None
            self.chromium_failures = 0 if ran_for > 120 else self.chromium_failures + 1
            delay = min(30, 2 ** min(self.chromium_failures, 5))
            self.chromium_next_start = time.monotonic() + delay
            self.transition(KioskState.ERROR, f"Chromium exited ({code}); restart in {delay}s")
            return
        if time.monotonic() - self.last_devtools_check < 15:
            return
        self.last_devtools_check = time.monotonic()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", 9222, timeout=2)
            connection.request("GET", "/json/list")
            response = connection.getresponse()
            targets = json.loads(response.read(262144)) if response.status == 200 else []
            connection.close()
            if not any(item.get("type") == "page" for item in targets):
                raise RuntimeError("no page renderer")
            self.devtools_failures = 0
        except (OSError, ValueError, RuntimeError, http.client.HTTPException):
            if time.monotonic() - self.chromium_started_at > 20:
                self.devtools_failures += 1
            if self.devtools_failures >= 3:
                LOG.warning("Chromium renderer health check failed; restarting")
                self.stop_chromium()

    def check_network(self) -> None:
        """Reload a Chromium error page once its origin becomes reachable again."""
        if time.monotonic() - self.last_network_check < 30 or not self.config:
            return
        self.last_network_check = time.monotonic()
        parsed = urlparse(self.config["url"])
        host = parsed.hostname
        if not host:
            return
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((host, port), timeout=3):
                pass
            was_offline = self.network_failures > 0
            self.network_failures = 0
            if was_offline:
                LOG.info("content origin reachable again; reloading Chromium")
                self.stop_chromium()
        except OSError:
            self.network_failures += 1
            if self.network_failures >= 2 and self.mpv is None:
                self.transition(KioskState.OFFLINE, f"content origin unavailable: {host}:{port}")

    def idle_milliseconds(self) -> int:
        try:
            completed = subprocess.run(
                [self.executable("xprintidle")],
                capture_output=True,
                timeout=2,
                check=True,
            )
            return int(completed.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            return 0

    def start_screensaver(self, *, manual: bool = False) -> bool:
        if self.mpv is not None:
            return True
        saver = self.config.get("screensaver", {})
        media_value = str(saver.get("media", ""))
        media = Path(media_value) if not is_remote_media(media_value) else None
        if not manual and (not saver.get("enabled") or self.saver_dismissed):
            return False
        if media is not None and not media.is_file():
            self.state_detail = f"screensaver media missing: {media_value}"
            self.mpv_next_start = time.monotonic() + 60
            self.write_status(force=True)
            return False
        if time.monotonic() < self.mpv_next_start:
            return False
        try:
            self.mpv = subprocess.Popen(
                [
                    self.executable("mpv"),
                    "--fullscreen",
                    "--ontop",
                    "--loop-file=inf",
                    "--no-border",
                    "--no-osc",
                    "--no-osd-bar",
                    "--cursor-autohide=always",
                    "--hwdec=auto-safe",
                    f"--input-conf={ROOT / 'config' / 'mpv-input.conf'}",
                    "--",
                    media_value,
                ],
                start_new_session=True,
            )
        except OSError as exc:
            LOG.error("could not start mpv: %s", exc)
            self.mpv_next_start = time.monotonic() + 30
            return False
        self.mpv_started_at = time.monotonic()
        self.transition(KioskState.SCREENSAVER, "screensaver playing")
        return True

    def stop_screensaver(self) -> None:
        self.terminate(self.mpv, "mpv")
        self.mpv = None
        self.saver_dismissed = True
        if self.chromium is not None and self.chromium.poll() is None:
            self.transition(KioskState.WEB_CONTENT, "Chromium running")

    def check_screensaver(self) -> None:
        idle = self.idle_milliseconds()
        if idle < 2000:
            self.saver_dismissed = False
        if self.mpv is not None:
            if idle < 1000 and time.monotonic() - self.mpv_started_at > 0.5:
                self.stop_screensaver()
                return
            code = self.mpv.poll()
            if code is not None:
                self.mpv = None
                if code == 0:
                    self.saver_dismissed = True
                else:
                    LOG.warning("mpv exited unexpectedly (%s)", code)
                    self.mpv_next_start = time.monotonic() + 10
                self.transition(KioskState.WEB_CONTENT, "screensaver stopped")
            return
        saver = self.config.get("screensaver", {})
        if saver.get("enabled") and idle >= int(saver.get("timeout_seconds", 300)) * 1000:
            self.start_screensaver()

    def process_commands(self) -> None:
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                return
            LOG.info("handling command: %s", command)
            if command == "reload":
                self.stop_chromium()
            elif command == "restart-playback":
                self.stop_chromium()
            elif command == "screensaver-start":
                self.saver_dismissed = False
                self.start_screensaver(manual=True)
            elif command == "screensaver-stop":
                self.stop_screensaver()

    def status(self) -> dict[str, Any]:
        chromium_running = self.chromium is not None and self.chromium.poll() is None
        mpv_running = self.mpv is not None and self.mpv.poll() is None
        return {
            "state": self.state.value,
            "state_detail": self.state_detail,
            "current_url": self.config.get("url", ""),
            "chromium": {"running": chromium_running, "pid": self.chromium.pid if chromium_running else None},
            "screensaver": {
                "running": mpv_running,
                "pid": self.mpv.pid if mpv_running else None,
                "enabled": self.config.get("screensaver", {}).get("enabled", False),
                "media_available": self.media_available(),
            },
        }

    def media_available(self) -> bool:
        value = str(self.config.get("screensaver", {}).get("media", ""))
        return bool(value) and (is_remote_media(value) or Path(value).is_file())

    def write_status(self, *, force: bool = False) -> None:
        status = self.status()
        now = time.monotonic()
        if force or status != self.last_status or now - self.last_status_write >= 30:
            atomic_write_json(STATUS_FILE, status, mode=0o644)
            self.last_status = status
            self.last_status_write = now

    @staticmethod
    def sd_notify(message: str) -> None:
        address = os.environ.get("NOTIFY_SOCKET")
        if not address:
            return
        if address.startswith("@"):
            address = "\0" + address[1:]
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify_socket:
                notify_socket.connect(address)
                notify_socket.sendall(message.encode("utf-8"))
        except OSError:
            pass

    def start_command_server(self) -> None:
        try:
            COMMAND_SOCKET.unlink()
        except FileNotFoundError:
            pass
        self.command_server = CommandServer(str(COMMAND_SOCKET), self.commands)
        os.chmod(COMMAND_SOCKET, 0o660)
        thread = threading.Thread(target=self.command_server.serve_forever, name="command-server", daemon=True)
        thread.start()

    def run(self) -> int:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        if not self.load_configuration(initial=True):
            return 1
        self.start_command_server()
        self.transition(KioskState.STARTING, "waiting for display")
        self.sd_notify("READY=1\nSTATUS=Starting playback")
        while not self.stop_event.is_set():
            self.process_commands()
            try:
                if CONFIG_FILE.stat().st_mtime_ns != self.config_mtime_ns:
                    self.load_configuration()
            except OSError:
                self.transition(KioskState.ERROR, "configuration file unavailable")
            self.check_chromium()
            self.check_network()
            self.check_screensaver()
            self.write_status()
            now = time.monotonic()
            if now - self.last_watchdog >= 10:
                self.sd_notify(f"WATCHDOG=1\nSTATUS={self.state.value}: {self.state_detail}")
                self.last_watchdog = now
            self.stop_event.wait(1)
        return 0

    def shutdown(self, *_args: object) -> None:
        self.stop_event.set()
        if self.command_server:
            self.command_server.shutdown()
        self.stop_screensaver()
        self.stop_chromium()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    daemon = KioskDaemon()
    signal.signal(signal.SIGTERM, daemon.shutdown)
    signal.signal(signal.SIGINT, daemon.shutdown)
    try:
        return daemon.run()
    finally:
        daemon.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
