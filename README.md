# CHASKI Web Kiosk

## QUICK START

You need a Raspberry Pi 3, microSD card, HDMI display, network connection, and another computer.

1. Use Raspberry Pi Imager to write **Raspberry Pi OS Lite (64-bit)** to the card. In Imager settings, choose a hostname, username/password, enable SSH, and configure Wi-Fi if Ethernet will not be used.
2. Boot the Pi, find its hostname or IP in your router, and connect: `ssh your-user@your-pi.local`
3. Install CHASKI:

   ```bash
   git clone <repository-url> chaski-web-kiosk
   cd chaski-web-kiosk
   sudo ./install.sh
   sudo reboot
   ```

4. After reboot, open `http://your-pi.local:8080` on another computer. Enter the content URL and select **Save URL**.

That is the complete normal installation. No desktop, package, X11, Chromium, service, user, or autologin configuration is required by hand.

CHASKI Web Kiosk turns Raspberry Pi OS Lite into a small web-signage appliance: Chromium fills the display, a local MP4 plays after inactivity, and a lightweight LAN page controls it. systemd owns every long-running process and automatically recovers it after failure.

## 1. Quick install

The supported development/checkout installation is:

```bash
git clone <repository-url> chaski-web-kiosk
cd chaski-web-kiosk
sudo ./install.sh
sudo reboot
```

`install.sh` is deliberately idempotent. Run it again after a pull to upgrade code and units. It preserves:

- `/opt/chaski-web-kiosk/config/kiosk.json`
- everything in `/opt/chaski-web-kiosk/media/`

The installer supports Raspberry Pi OS/Debian 11 or newer on `armhf` or `arm64`. It stops with a clear error on other systems rather than partially configuring them.

## 2. Fresh Raspberry Pi setup

Use Raspberry Pi OS Lite, not the desktop image. Boot once and SSH in using the account created in Raspberry Pi Imager. The installer will:

- install Chromium, minimal Xorg, Openbox, mpv, cursor hiding, Avahi, and the Python runtime;
- create the locked-down `chaski` service account;
- create and permission `/opt/chaski-web-kiosk` and `/var/lib/chaski-web-kiosk`;
- install and enable all systemd units;
- reserve tty1 for the kiosk display and select `graphical.target`;
- disable X screen blanking and DPMS;
- install the narrowly scoped reboot permission and status helper;
- start the services and validate their installation.

The installation does not wait for internet during later boots. Internet is only needed during installation to download apt packages and the repository.

## 3. Architecture

There is no desktop environment and no application database.

```text
systemd
├── chaski-web-kiosk-display.service  → Xorg :0 → Openbox + unclutter
├── chaski-web-kiosk.service   → Python supervisor
│   ├── Chromium kiosk
│   └── mpv (only while idle)
└── chaski-web-kiosk-control.service  → HTTP management/API on the LAN
```

The supervisor follows an explicit state machine:

```text
BOOT → STARTING → WEB_CONTENT ⇄ SCREENSAVER
                    ↑               |
                 OFFLINE / ERROR ───┘
```

Chromium remains underneath mpv. Any normal keyboard, mouse, or touch input exits mpv so Chromium is visible immediately. Chromium and mpv run as child process groups; the supervisor reaps and replaces them without leaving duplicate or zombie processes. systemd restarts X, the kiosk supervisor, and the control server. A systemd watchdog also verifies that the kiosk event loop remains responsive.

The kiosk polls the configured origin lightly. If boot occurs offline, playback and management still start. Once the origin becomes reachable, Chromium is restarted once to replace its network error page. No boot target depends on `network-online.target`.

Persistent data lives under `/opt/chaski-web-kiosk/config` and `/opt/chaski-web-kiosk/media`. Browser cache, the command socket, and status snapshots live under `/run/chaski-web-kiosk` (tmpfs) to avoid SD-card wear. The persistent Chromium profile is only used for essential browser state; its disk cache is redirected to `/run`.

## 4. Configuration

The persistent file is `/opt/chaski-web-kiosk/config/kiosk.json`:

```json
{
  "kiosk_name": "MUSEO-KIOSK-01",
  "url": "http://localhost:8080/welcome",
  "screensaver": {
    "enabled": true,
    "timeout_seconds": 300,
    "media": "/opt/chaski-web-kiosk/media/screensaver.mp4"
  },
  "control_port": 8080
}
```

Use the management page for normal changes. It validates values and uses an atomic write (`fsync`, temporary file, rename) so sudden power loss cannot leave a half-written configuration. Direct edits are detected automatically, but must remain valid JSON. URLs must be absolute HTTP(S) URLs without embedded credentials. Screensaver files must be `.mp4` files inside `/opt/chaski-web-kiosk/media`.

The default name is the Pi hostname. The default URL is the built-in welcome page, so first boot always has useful content. If `control_port` is changed through the API, the management service restarts itself on the new port.

## 5. Web control

Open either address from the same LAN:

```text
http://KIOSK_IP:8080/
http://hostname.local:8080/
```

The page shows kiosk identity, hostname, IP, OS uptime, software version, state, URL, Chromium health, and screensaver health. It can save a URL, reload or restart playback, upload and select an MP4, configure and preview the screensaver, manage supported network connections, and reboot the Pi.

API endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/status` | Live identity and health |
| GET | `/api/config` | Persistent configuration |
| POST | `/api/config` | Merge validated configuration fields |
| POST | `/api/url` | Validate, save, and apply `{"url":"https://…"}` |
| POST | `/api/reload` | Restart Chromium on the same URL |
| POST | `/api/restart-playback` | Restart web playback |
| POST | `/api/screensaver/start` | Preview configured media |
| POST | `/api/screensaver/stop` | Stop the screensaver |
| POST | `/api/media` | Stream an MP4 upload (maximum 2 GiB) and select it |
| GET | `/api/network` | NetworkManager availability and interface status |
| POST | `/api/network` | Validate and asynchronously apply DHCP/static/Wi-Fi settings |
| POST | `/api/reboot` | Reboot through the one-command sudo policy |

The server runs as `chaski`, never root. It limits request size, checks same-origin browser requests, validates all input, prevents media path traversal, and exposes no generic command execution. The design assumes a trusted LAN; place it behind a firewall/VLAN if the network is not trusted.

### Network settings

The control page supports Ethernet and Wi-Fi connections managed by NetworkManager. Select an interface and choose automatic DHCP or a static IPv4 address. For Wi-Fi, enter an SSID and, when required, its password. The password is sent only to NetworkManager and is never returned by the API or written to CHASKI configuration.

Network changes are validated by an unprivileged pass before the HTTP response is sent. One second later, a root-owned helper applies the exact validated schema through `nmcli`; it never invokes a shell. Sudo permits only that helper's `apply` operation. Applying settings can disconnect the management browser, so the page shows the requested static reconnect address first.

Current Raspberry Pi OS Lite releases use NetworkManager. On an older image using `dhcpcd`, CHASKI reports network editing as unavailable and leaves the operating system's network files untouched. This prevents an upgrade from unexpectedly replacing the active networking stack over SSH.

## 6. Screensaver

At the configured idle threshold (default 300 seconds), mpv opens the configured H.264 MP4 fullscreen, above Chromium, looping without controls. The source may be a local file under `/opt/chaski-web-kiosk/media` or a direct HTTP(S) video URL. Local media is recommended for reliable offline playback. `hwdec=auto-safe` uses hardware decoding when the Pi's driver and file support it.

Keyboard, mouse movement/click, and touchscreen input quit mpv. A clean exit is treated as user dismissal until activity resets the idle timer. An unexpected mpv failure is retried with a delay. A missing or corrupt file is reported in status and logs without disturbing web playback or causing a rapid restart loop.

## 7. Media replacement

The simplest option is **Upload & select** in the Screensaver panel. Uploads are streamed to disk rather than held in memory, checked for an MP4 container, atomically moved into `/opt/chaski-web-kiosk/media`, and limited to 2 GiB. Uploading a filename that already exists explicitly replaces that media file.

Alternatively, copy a video over SSH:

```bash
scp screensaver.mp4 your-user@your-pi.local:/tmp/screensaver.mp4
ssh your-user@your-pi.local
sudo install -o chaski -g chaski -m 0644 /tmp/screensaver.mp4 /opt/chaski-web-kiosk/media/screensaver.mp4
```

Recommended Raspberry Pi 3 encoding: H.264, MP4 container, no more than 1920×1080, with a hardware-decodable profile. Other local files may be added under `/opt/chaski-web-kiosk/media/` and selected from the control page.

## 8. systemctl commands

```bash
sudo chaski-web-kiosk-status
sudo systemctl status chaski-web-kiosk-display chaski-web-kiosk chaski-web-kiosk-control
sudo systemctl restart chaski-web-kiosk
sudo systemctl restart chaski-web-kiosk-display chaski-web-kiosk
sudo systemctl restart chaski-web-kiosk-control
```

Restart `chaski-web-kiosk` for playback only. Restart both display and kiosk services after X/HDMI trouble. Services use `Restart=always` and bounded restart delays.

## 9. journalctl commands

```bash
sudo journalctl -u chaski-web-kiosk -f
sudo journalctl -u chaski-web-kiosk-display -f
sudo journalctl -u chaski-web-kiosk-control -f
sudo journalctl -u 'chaski-*' --since today
```

Chromium and mpv inherit the kiosk service journal, so their startup and decoder errors appear with `chaski-web-kiosk`.

## 10. Updates

From the original checkout:

```bash
cd chaski-web-kiosk
git pull --ff-only
sudo ./install.sh
```

This updates application code, static files, helper commands, and systemd units, then restarts and validates services. It never overwrites `kiosk.json`, any media file, or the installer package-history record.

The optional `chaski-web-kiosk-update` helper is only installed when the repository itself is deliberately located at `/opt/chaski-web-kiosk/source`; this avoids guessing which checkout should be pulled.

## 11. Uninstallation

Default removal preserves configuration and media:

```bash
sudo ./uninstall.sh
```

Complete removal:

```bash
sudo ./uninstall.sh --purge
```

Also remove only the direct packages that were absent before the first CHASKI install:

```bash
sudo ./uninstall.sh --purge --remove-deps
```

Dependency removal never invokes `autoremove`. The first installation records the prior boot target, tty1/getty state, and X wrapper configuration. The uninstaller restores those settings. If the record is unavailable, it prints the two commands normally used to restore console boot.

## 12. Troubleshooting

**Management page works but HDMI is blank**

```bash
sudo systemctl status chaski-web-kiosk-display chaski-web-kiosk
sudo journalctl -u chaski-web-kiosk-display -b
```

Confirm HDMI was connected during boot, then restart both units. Some displays require a forced HDMI mode in `/boot/firmware/config.txt`; use values appropriate to the display.

**Chromium repeatedly restarts**

Check `sudo journalctl -u chaski-web-kiosk -b`. Confirm the Pi clock is correct for HTTPS sites, the URL is valid, and memory is not exhausted. Renderer health requires local port 9222; do not bind another program there.

**The web page is unavailable**

Run `sudo systemctl status chaski-web-kiosk-control`, then `sudo chaski-web-kiosk-status`. Verify port 8080 is not used by another service. `.local` names require mDNS support on the client; use the numeric IP if necessary.

**Screensaver does not start**

The initial installation includes a small H.264 default loop. Confirm `/opt/chaski-web-kiosk/media/screensaver.mp4` exists and is readable by `chaski`, then use **Preview**. Decoder details are in the kiosk journal.

**Configuration is invalid**

Compare it with `config/kiosk.json.example` in the checkout. Re-running the installer preserves invalid local configuration by design; correct it rather than reinstalling.

## 13. Cloning multiple kiosks

Prepare each Pi from the same image or repeat Quick Start, but give every Pi a unique hostname in Raspberry Pi Imager. After installation, change `kiosk_name` through `kiosk.json` or the API. Good identities include `KIOSK-001`, `SALA-04`, and `MUSEO-NORTE-02`.

Do not clone a running Chromium profile. If cloning an already-installed SD image, delete `/var/lib/chaski-web-kiosk/chromium` before first boot of the clone and recreate it owned by `chaski`, or install CHASKI after cloning the base Raspberry Pi OS image. Always regenerate SSH host keys when distributing a cloned operating-system image.

## 14. Recommended Raspberry Pi Imager settings

- OS: Raspberry Pi OS Lite (64-bit), current stable release
- Hostname: unique, lowercase, DNS-safe (for example `museo-norte-02`)
- Username/password: unique administrative account; do not use `chaski`
- SSH: enabled, preferably public-key authentication
- Wi-Fi: configure only if Ethernet is unavailable; set the correct regulatory country
- Locale/timezone/keyboard: match the installation site
- Telemetry: optional

For unattended exhibits, use wired Ethernet where possible, a quality power supply, a high-endurance SD card, adequate ventilation, and a tested HDMI cable. Disable password SSH after verifying key access. Keep the management port on a trusted operations VLAN.

## Development checks

Run the repository checks on any machine with Python 3:

```bash
./tests/run.sh
```

The suite validates configuration merging/rejection, atomic persistence, HTTP input behavior, shell syntax, and Python compilation. Final appliance acceptance should additionally cover a fresh Pi install, a repeated install, upgrade preservation, offline boot, abrupt power removal, Chromium/mpv kills, input dismissal, and a multi-day soak test on the exact Pi/display/media combination.

## Credits

CHASKI Web Kiosk was conceived as a lightweight, reliable digital-signage appliance for Raspberry Pi and coded with OpenAI Codex.
