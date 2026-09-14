"""Shared configuration, status, and IPC helpers for CHASKI Web Kiosk."""

from __future__ import annotations

import json
import os
import socket
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(os.environ.get("CHASKI_ROOT", "/opt/chaski-player"))
RUNTIME = Path(os.environ.get("CHASKI_RUNTIME", "/run/chaski-player"))
CONFIG_FILE = ROOT / "config" / "player.json"
MEDIA_DIR = ROOT / "media"
STATUS_FILE = RUNTIME / "status.json"
COMMAND_SOCKET = RUNTIME / "player.sock"
VERSION_FILE = ROOT / "VERSION"


def default_config() -> dict[str, Any]:
    return {
        "player_name": socket.gethostname(),
        "url": "http://localhost:8080/welcome",
        "screensaver": {
            "enabled": True,
            "timeout_seconds": 300,
            "media": str(MEDIA_DIR / "screensaver.mp4"),
        },
        "control_port": 8080,
    }


def validate_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("URL must be a string of at most 2048 characters")
    value = value.strip()
    if any(ord(character) < 32 for character in value):
        raise ValueError("URL must not contain control characters")
    parsed = urlparse(value)
    try:
        host = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ValueError("URL contains an invalid host or port") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not host:
        raise ValueError("URL must be an absolute http:// or https:// URL")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed")
    return value


def validate_media_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Screensaver media must be a local path or HTTP(S) URL")
    value = value.strip()
    if urlparse(value).scheme in {"http", "https"}:
        return validate_url(value)
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = MEDIA_DIR / candidate
    resolved = candidate.resolve(strict=False)
    media_root = MEDIA_DIR.resolve(strict=False)
    try:
        resolved.relative_to(media_root)
    except ValueError as exc:
        raise ValueError(f"Screensaver media must be inside {MEDIA_DIR}") from exc
    if resolved.suffix.lower() != ".mp4":
        raise ValueError("Screensaver media must be an MP4 file")
    return str(resolved)


def is_remote_media(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


def validate_config(raw: Any, *, partial: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Configuration must be a JSON object")
    allowed = {"player_name", "url", "screensaver", "control_port"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"Unknown configuration field: {sorted(unknown)[0]}")

    result = {} if partial else default_config()
    if "player_name" in raw:
        name = raw["player_name"]
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
            raise ValueError("Player name must contain 1 to 64 characters")
        result["player_name"] = name.strip()
    if "url" in raw:
        result["url"] = validate_url(raw["url"])
    if "control_port" in raw:
        port = raw["control_port"]
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ValueError("Control port must be an integer from 1024 to 65535")
        result["control_port"] = port
    if "screensaver" in raw:
        saver = raw["screensaver"]
        if not isinstance(saver, dict):
            raise ValueError("screensaver must be an object")
        saver_allowed = {"enabled", "timeout_seconds", "media"}
        saver_unknown = set(saver) - saver_allowed
        if saver_unknown:
            raise ValueError(f"Unknown screensaver field: {sorted(saver_unknown)[0]}")
        saver_result = {} if partial else deepcopy(default_config()["screensaver"])
        if "enabled" in saver:
            if not isinstance(saver["enabled"], bool):
                raise ValueError("screensaver.enabled must be true or false")
            saver_result["enabled"] = saver["enabled"]
        if "timeout_seconds" in saver:
            timeout = saver["timeout_seconds"]
            if isinstance(timeout, bool) or not isinstance(timeout, int) or not 10 <= timeout <= 86400:
                raise ValueError("Screensaver timeout must be from 10 to 86400 seconds")
            saver_result["timeout_seconds"] = timeout
        if "media" in saver:
            saver_result["media"] = validate_media_path(saver["media"])
        result["screensaver"] = saver_result

    if not partial:
        missing = allowed - set(result)
        if missing:
            raise ValueError(f"Missing configuration field: {sorted(missing)[0]}")
    return result


def merge_config(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(current)
    for key, value in validate_config(patch, partial=True).items():
        if key == "screensaver":
            merged.setdefault("screensaver", {}).update(value)
        else:
            merged[key] = value
    return validate_config(merged)


def load_config(path: Path = CONFIG_FILE) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return validate_config(json.load(handle))


def atomic_write_json(path: Path, value: Any, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path, fallback: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def software_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return str(sock.getsockname()[0])
    except OSError:
        return "unavailable"
    finally:
        sock.close()


def system_uptime() -> int:
    try:
        return int(float(Path("/proc/uptime").read_text(encoding="ascii").split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def send_command(command: str, timeout: float = 2.0) -> dict[str, Any]:
    payload = json.dumps({"command": command}).encode("utf-8") + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(COMMAND_SOCKET))
        sock.sendall(payload)
        response = b""
        while b"\n" not in response and len(response) < 65536:
            block = sock.recv(4096)
            if not block:
                break
            response += block
    if not response:
        raise RuntimeError("Player daemon did not respond")
    return json.loads(response.split(b"\n", 1)[0].decode("utf-8"))
