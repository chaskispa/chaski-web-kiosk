"""Dependency-free LAN management server for CHASKI Web Kiosk."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import socket
import subprocess
import tempfile
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .common import (
    CONFIG_FILE,
    MEDIA_DIR,
    ROOT,
    STATUS_FILE,
    atomic_write_json,
    load_config,
    local_ip,
    merge_config,
    read_json,
    send_command,
    software_version,
    system_uptime,
    validate_url,
)


LOG = logging.getLogger("chaski-web-kiosk-control")
STATIC_DIR = ROOT / "static"
MAX_BODY = 65536
MAX_UPLOAD = 2 * 1024 * 1024 * 1024
NETWORK_HELPER = "/usr/local/libexec/chaski-web-kiosk-network"


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "CHASKI-Web-Kiosk"

    def log_message(self, fmt: str, *args: object) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")

    def send_bytes(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status: int, value: Any) -> None:
        self.send_bytes(status, json.dumps(value).encode("utf-8"), "application/json; charset=utf-8")

    def send_static(self, filename: str, content_type: str) -> None:
        try:
            data = (STATIC_DIR / filename).read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_bytes(HTTPStatus.OK, data, content_type)

    def welcome(self) -> None:
        config = load_config()
        address = local_ip()
        body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#dededd"><title>CHASKI Web Kiosk</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body class="welcome"><div class="signal-line" aria-hidden="true"></div>
<aside class="brand-rail" aria-label="CHASKI"><img src="/static/Logo.png" alt="CHASKI"></aside>
<div class="welcome-shell"><header class="welcome-header"><span>[0.1]</span><span>WEB KIOSK / DISPLAY</span></header>
<main class="welcome-main"><section class="welcome-lead"><p class="welcome-kicker"><span>[1]</span> SYSTEM STATUS</p>
<div><h1>READY.</h1><p class="ready">CHASKI Web Kiosk is online</p></div></section>
<section class="welcome-details"><div class="detail"><p>Management</p>
<strong id="welcome-management">http://{html.escape(address)}:{config['control_port']}</strong></div>
<div class="detail"><p>Current IP</p><strong id="welcome-ip">{html.escape(address)}</strong></div></section></main>
<footer class="welcome-footer"><span>CHASKI Web Kiosk</span><span>Designed by CHASKI · Coded with OpenAI Codex</span></footer></div>
<script src="/static/welcome.js"></script></body></html>"""
        self.send_bytes(HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8")

    def api_status(self) -> dict[str, Any]:
        config = load_config()
        status = read_json(STATUS_FILE, {})
        status.update(
            {
                "kiosk_name": config["kiosk_name"],
                "hostname": socket.gethostname(),
                "ip": local_ip(),
                "uptime_seconds": system_uptime(),
                "software_version": software_version(),
                "configured_url": config["url"],
            }
        )
        return status

    @staticmethod
    def network_helper(command: str, payload: dict[str, Any] | None = None, *, privileged: bool = False) -> dict[str, Any]:
        argv = [NETWORK_HELPER, command]
        if privileged:
            argv = ["/usr/bin/sudo", "-n", *argv]
        completed = subprocess.run(
            argv,
            input=json.dumps(payload).encode("utf-8") if payload is not None else None,
            capture_output=True,
            timeout=30,
            check=False,
        )
        output = completed.stdout.decode("utf-8", errors="replace").strip()
        try:
            result = json.loads(output) if output else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError("Network helper returned an invalid response") from exc
        if completed.returncode != 0:
            error = result.get("error") or completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(error or "Network operation failed")
        return result

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/":
                self.send_static("index.html", "text/html; charset=utf-8")
            elif path == "/static/style.css":
                self.send_static("style.css", "text/css; charset=utf-8")
            elif path == "/static/app.js":
                self.send_static("app.js", "text/javascript; charset=utf-8")
            elif path == "/static/welcome.js":
                self.send_static("welcome.js", "text/javascript; charset=utf-8")
            elif path == "/static/Logo.png":
                self.send_static("Logo.png", "image/png")
            elif path == "/welcome":
                self.welcome()
            elif path == "/api/status":
                self.send_json(HTTPStatus.OK, self.api_status())
            elif path == "/api/config":
                self.send_json(HTTPStatus.OK, load_config())
            elif path == "/api/network":
                self.send_json(HTTPStatus.OK, self.network_helper("status"))
            else:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            LOG.exception("GET %s failed", path)
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        host = self.headers.get("Host", "")
        return parsed.scheme in {"http", "https"} and parsed.netloc == host

    def read_body(self) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length <= 0 or length > MAX_BODY:
            raise ValueError(f"Request body must be from 1 to {MAX_BODY} bytes")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    def run_command(self, command: str) -> None:
        try:
            response = send_command(command)
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error", "command rejected")))
            self.send_json(HTTPStatus.ACCEPTED, response)
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})

    def receive_media(self) -> None:
        content_type = self.headers.get_content_type()
        if content_type not in {"video/mp4", "application/mp4", "application/octet-stream"}:
            raise ValueError("Upload must be an MP4 file")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length <= 0 or length > MAX_UPLOAD:
            raise ValueError("Video must be between 1 byte and 2 GiB")
        original_name = unquote(self.headers.get("X-Filename", "screensaver.mp4"))
        filename = Path(original_name).name
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,126}\.mp4", filename, flags=re.IGNORECASE):
            raise ValueError("Use an MP4 filename containing only letters, numbers, spaces, dots, dashes, or underscores")

        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".upload-", suffix=".mp4", dir=MEDIA_DIR)
        target = MEDIA_DIR / filename
        remaining = length
        header = b""
        try:
            with os.fdopen(descriptor, "wb") as handle:
                while remaining:
                    block = self.rfile.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError("Upload ended before Content-Length was received")
                    if len(header) < 12:
                        header += block[: 12 - len(header)]
                    handle.write(block)
                    remaining -= len(block)
                handle.flush()
                os.fsync(handle.fileno())
            if len(header) < 12 or header[4:8] != b"ftyp":
                raise ValueError("The uploaded file is not an MP4 container")
            os.chmod(temporary, 0o644)
            os.replace(temporary, target)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

        config = load_config()
        config["screensaver"]["media"] = str(target)
        atomic_write_json(CONFIG_FILE, config)
        try:
            send_command("screensaver-stop")
        except (OSError, RuntimeError, json.JSONDecodeError):
            LOG.warning("video uploaded but kiosk supervisor was unavailable")
        self.send_json(HTTPStatus.CREATED, {"ok": True, "filename": filename, "media": str(target)})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self.same_origin():
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "Cross-origin request rejected"})
            return
        try:
            if path == "/api/media":
                self.receive_media()
            elif path == "/api/config":
                patch = self.read_body()
                current = load_config()
                updated = merge_config(current, patch)
                port_changed = updated["control_port"] != current["control_port"]
                atomic_write_json(CONFIG_FILE, updated)
                self.run_command("restart-playback" if updated["url"] != current["url"] else "reload")
                if port_changed:
                    LOG.warning("control port change takes effect after chaski-web-kiosk-control restarts")
                    threading.Timer(1.0, lambda: os._exit(0)).start()
            elif path == "/api/url":
                body = self.read_body()
                if set(body) != {"url"}:
                    raise ValueError("Request must contain only url")
                current = load_config()
                current["url"] = validate_url(body["url"])
                atomic_write_json(CONFIG_FILE, current)
                self.run_command("restart-playback")
            elif path == "/api/reload":
                self.run_command("reload")
            elif path == "/api/restart-playback":
                self.run_command("restart-playback")
            elif path == "/api/screensaver/start":
                self.run_command("screensaver-start")
            elif path == "/api/screensaver/stop":
                self.run_command("screensaver-stop")
            elif path == "/api/reboot":
                self.send_json(HTTPStatus.ACCEPTED, {"ok": True, "message": "Reboot scheduled"})
                threading.Timer(1.0, self.reboot).start()
            elif path == "/api/network":
                body = self.read_body()
                validated = self.network_helper("validate", body)
                self.send_json(
                    HTTPStatus.ACCEPTED,
                    {"ok": True, "message": "Network change scheduled", "reconnect_address": validated.get("reconnect_address")},
                )
                threading.Timer(1.0, self.apply_network, args=(body,)).start()
            else:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except OSError as exc:
            LOG.exception("POST %s failed", path)
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    @staticmethod
    def reboot() -> None:
        try:
            subprocess.run(
                ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "reboot"],
                timeout=10,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            LOG.exception("reboot request failed")

    @staticmethod
    def apply_network(payload: dict[str, Any]) -> None:
        try:
            Handler.network_helper("apply", payload, privileged=True)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            LOG.exception("network change failed")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = load_config()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOG.error("cannot load configuration: %s", exc)
        return 1
    for abandoned_upload in MEDIA_DIR.glob(".upload-*.mp4"):
        try:
            abandoned_upload.unlink()
        except OSError:
            LOG.warning("could not remove abandoned upload %s", abandoned_upload)
    address = ("0.0.0.0", config["control_port"])
    server = ControlServer(address, Handler)
    LOG.info("management listening on %s:%s", *address)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
