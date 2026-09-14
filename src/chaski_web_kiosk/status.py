"""Human-readable command-line status helper."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .common import load_config


def main() -> int:
    config = load_config()
    url = f"http://127.0.0.1:{config['control_port']}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            status = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"CHASKI Web Kiosk status unavailable: {exc}")
        return 1
    saver = status.get("screensaver", {})
    chromium = status.get("chromium", {})
    print(f"Kiosk:       {status.get('kiosk_name', 'unknown')}")
    print(f"State:       {status.get('state', 'unknown')} ({status.get('state_detail', '')})")
    print(f"Address:     http://{status.get('ip', 'unavailable')}:{config['control_port']}")
    print(f"URL:         {status.get('configured_url', '')}")
    print(f"Chromium:    {'running' if chromium.get('running') else 'stopped'}")
    print(f"Screensaver: {'playing' if saver.get('running') else 'idle'}")
    print(f"Version:     {status.get('software_version', 'unknown')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
