#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_NAME="CHASKI Web Kiosk"
readonly SERVICE_USER="chaski"
readonly INSTALL_ROOT="/opt/chaski-web-kiosk"
readonly STATE_HOME="/var/lib/chaski-web-kiosk"
readonly INSTALL_STATE="$STATE_HOME/install-state"
readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PACKAGE_RECORD="$INSTALL_ROOT/config/installed-packages.txt"

log() { printf '  %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

if [[ $EUID -ne 0 ]]; then
    die "Run this installer as root: sudo ./install.sh"
fi

[[ -r /etc/os-release ]] || die "Cannot identify this operating system."
# shellcheck disable=SC1091
source /etc/os-release
os_family="${ID:-} ${ID_LIKE:-}"
[[ "$os_family" == *debian* || "$os_family" == *raspbian* ]] || \
    die "Unsupported OS '${PRETTY_NAME:-unknown}'. Use Raspberry Pi OS or Debian."

arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
case "$arch" in
    armhf|arm64|armv7l|aarch64) ;;
    *) die "Unsupported architecture '$arch'. CHASKI Web Kiosk requires ARM Linux (armhf or arm64)." ;;
esac

version_id="${VERSION_ID:-0}"
version_major="${version_id%%.*}"
[[ "$version_major" =~ ^[0-9]+$ ]] || die "Cannot identify the Debian version."
(( version_major >= 11 )) || die "Debian/Raspberry Pi OS 11 or newer is required."

for required in src/chaski_web_kiosk/daemon.py static/index.html static/Logo.png systemd/chaski-web-kiosk.service scripts/chaski-web-kiosk-network scripts/chaski-web-kiosk-status config/chromium-policy.json; do
    [[ -f "$SOURCE_DIR/$required" ]] || die "Installer source is incomplete: missing $required"
done

printf '\nInstalling %s on %s (%s)...\n\n' "$APP_NAME" "${PRETTY_NAME:-Debian}" "$arch"

# Save host boot/X settings before package installation or appliance configuration.
install -d -o root -g root -m 0755 "$STATE_HOME"
install -d -o root -g root -m 0700 "$INSTALL_STATE"
if [[ ! -e "$INSTALL_STATE/recorded" ]]; then
    systemctl get-default > "$INSTALL_STATE/default-target" 2>/dev/null || printf '%s\n' multi-user.target > "$INSTALL_STATE/default-target"
    systemctl is-enabled getty@tty1.service > "$INSTALL_STATE/getty-state" 2>/dev/null || true
    systemctl is-enabled avahi-daemon.service > "$INSTALL_STATE/avahi-state" 2>/dev/null || true
    if [[ -e /etc/X11/Xwrapper.config ]]; then
        cp -a /etc/X11/Xwrapper.config "$INSTALL_STATE/Xwrapper.config"
        touch "$INSTALL_STATE/xwrapper-existed"
    fi
    touch "$INSTALL_STATE/recorded"
fi

export DEBIAN_FRONTEND=noninteractive
log "Refreshing package indexes..."
apt-get update -qq

chromium_package=""
for candidate in chromium chromium-browser; do
    if apt-cache show "$candidate" >/dev/null 2>&1; then
        chromium_package="$candidate"
        break
    fi
done
[[ -n "$chromium_package" ]] || die "No Chromium package is available from the configured apt repositories."

packages=(
    "$chromium_package"
    python3
    xserver-xorg-core
    xserver-xorg-legacy
    xserver-xorg-input-libinput
    xinit
    x11-xserver-utils
    x11-utils
    xprintidle
    openbox
    unclutter
    mpv
    dbus-x11
    fonts-dejavu-core
    sudo
    ca-certificates
    curl
    avahi-daemon
)

new_packages=()
for package in "${packages[@]}"; do
    if ! dpkg-query -W -f='${db:Status-Abbrev}' "$package" 2>/dev/null | grep -q '^ii'; then
        new_packages+=("$package")
    fi
done

log "Installing Chromium, X11, mpv, and runtime packages..."
apt-get install -y --no-install-recommends "${packages[@]}"

if ! getent passwd "$SERVICE_USER" >/dev/null; then
    log "Creating dedicated service account..."
    useradd --system --create-home --home-dir "$STATE_HOME" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

supplementary_groups=()
for group in audio video input render; do
    getent group "$group" >/dev/null && supplementary_groups+=("$group")
done
if ((${#supplementary_groups[@]})); then
    groups_csv="$(IFS=,; printf '%s' "${supplementary_groups[*]}")"
    usermod -a -G "$groups_csv" "$SERVICE_USER"
fi

log "Installing application files..."
install -d -o root -g root -m 0755 "$INSTALL_ROOT" "$INSTALL_ROOT/app" "$INSTALL_ROOT/app/chaski_web_kiosk" "$INSTALL_ROOT/static" "$INSTALL_ROOT/bin" /usr/local/libexec
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$INSTALL_ROOT/config"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 "$INSTALL_ROOT/media" "$STATE_HOME/chromium"
chown "$SERVICE_USER:$SERVICE_USER" "$STATE_HOME"
chmod 0750 "$STATE_HOME"

install -o root -g root -m 0644 "$SOURCE_DIR/VERSION" "$INSTALL_ROOT/VERSION"
install -o root -g root -m 0644 "$SOURCE_DIR/src/chaski_web_kiosk/"*.py "$INSTALL_ROOT/app/chaski_web_kiosk/"
install -o root -g root -m 0644 "$SOURCE_DIR/static/index.html" "$SOURCE_DIR/static/style.css" "$SOURCE_DIR/static/app.js" "$SOURCE_DIR/static/welcome.js" "$SOURCE_DIR/static/Logo.png" "$INSTALL_ROOT/static/"
install -o root -g root -m 0755 "$SOURCE_DIR/scripts/xsession" "$INSTALL_ROOT/bin/xsession"
install -o root -g root -m 0755 "$SOURCE_DIR/scripts/chaski-web-kiosk-network" /usr/local/libexec/chaski-web-kiosk-network
install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0640 "$SOURCE_DIR/config/mpv-input.conf" "$INSTALL_ROOT/config/mpv-input.conf"

# Managed policy is authoritative even when Chromium changes or ignores UI flags.
install -d -o root -g root -m 0755 /etc/chromium/policies/managed /etc/chromium-browser/policies/managed
install -o root -g root -m 0644 "$SOURCE_DIR/config/chromium-policy.json" /etc/chromium/policies/managed/chaski-web-kiosk.json
install -o root -g root -m 0644 "$SOURCE_DIR/config/chromium-policy.json" /etc/chromium-browser/policies/managed/chaski-web-kiosk.json

if [[ ! -e "$INSTALL_ROOT/config/kiosk.json" ]]; then
    hostname_json="$(hostname | tr -cd 'A-Za-z0-9._-')"
    [[ -n "$hostname_json" ]] || hostname_json="CHASKI-WEB-KIOSK"
    default_config_file="$(mktemp "$INSTALL_ROOT/config/.kiosk.json.XXXXXX")"
    KIOSK_NAME="$hostname_json" DEFAULT_CONFIG_FILE="$default_config_file" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["DEFAULT_CONFIG_FILE"])
config = {
    "kiosk_name": os.environ["KIOSK_NAME"],
    "url": "http://localhost:8080/welcome",
    "screensaver": {
        "enabled": True,
        "timeout_seconds": 30,
        "media": "/opt/chaski-web-kiosk/media/screensaver.mp4",
    },
    "control_port": 8080,
}
with path.open("w", encoding="utf-8") as handle:
    handle.write(json.dumps(config, indent=2) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
    chown "$SERVICE_USER:$SERVICE_USER" "$default_config_file"
    chmod 0640 "$default_config_file"
    mv -f "$default_config_file" "$INSTALL_ROOT/config/kiosk.json"
else
    log "Preserving existing kiosk.json."
fi

if [[ -f "$SOURCE_DIR/media/README.txt" && ! -e "$INSTALL_ROOT/media/README.txt" ]]; then
    install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0644 "$SOURCE_DIR/media/README.txt" "$INSTALL_ROOT/media/README.txt"
fi
if [[ -f "$SOURCE_DIR/media/screensaver.mp4" && ! -e "$INSTALL_ROOT/media/screensaver.mp4" ]]; then
    default_media_file="$(mktemp "$INSTALL_ROOT/media/.screensaver.mp4.XXXXXX")"
    install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0644 "$SOURCE_DIR/media/screensaver.mp4" "$default_media_file"
    mv -f "$default_media_file" "$INSTALL_ROOT/media/screensaver.mp4"
fi

if ((${#new_packages[@]})); then
    touch "$PACKAGE_RECORD"
    printf '%s\n' "${new_packages[@]}" >> "$PACKAGE_RECORD"
    sort -u -o "$PACKAGE_RECORD" "$PACKAGE_RECORD"
    chown root:root "$PACKAGE_RECORD"
    chmod 0644 "$PACKAGE_RECORD"
fi

# Permit the service account to start X on the appliance VT. TCP listening remains disabled.
printf '%s\n' 'allowed_users=anybody' 'needs_root_rights=yes' > /etc/X11/Xwrapper.config
chown root:root /etc/X11/Xwrapper.config
chmod 0644 /etc/X11/Xwrapper.config

log "Installing system services and helper commands..."
for unit in chaski-web-kiosk-display.service chaski-web-kiosk.service chaski-web-kiosk-control.service; do
    install -o root -g root -m 0644 "$SOURCE_DIR/systemd/$unit" "/etc/systemd/system/$unit"
done
install -o root -g root -m 0755 "$SOURCE_DIR/scripts/chaski-web-kiosk-status" /usr/local/bin/chaski-web-kiosk-status

if [[ "$SOURCE_DIR" == "$INSTALL_ROOT/source" && -d "$SOURCE_DIR/.git" ]]; then
    install -o root -g root -m 0755 "$SOURCE_DIR/scripts/chaski-web-kiosk-update" /usr/local/sbin/chaski-web-kiosk-update
else
    rm -f /usr/local/sbin/chaski-web-kiosk-update
fi

cat > /etc/sudoers.d/chaski-web-kiosk <<'EOF'
# CHASKI control panel may only request a system reboot.
chaski ALL=(root) NOPASSWD: /usr/bin/systemctl reboot
# Network settings pass through a root-owned helper with strict JSON validation.
chaski ALL=(root) NOPASSWD: /usr/local/libexec/chaski-web-kiosk-network apply
EOF
chmod 0440 /etc/sudoers.d/chaski-web-kiosk
visudo -cf /etc/sudoers.d/chaski-web-kiosk >/dev/null || die "Generated sudo policy failed validation."

systemctl disable --now getty@tty1.service >/dev/null 2>&1 || true
systemctl set-default graphical.target >/dev/null
systemctl daemon-reload
systemctl enable chaski-web-kiosk-display.service chaski-web-kiosk.service chaski-web-kiosk-control.service avahi-daemon.service >/dev/null
systemctl restart avahi-daemon.service || log "Warning: Avahi did not start; numeric-IP management will still work."
systemctl restart chaski-web-kiosk-display.service chaski-web-kiosk.service chaski-web-kiosk-control.service

log "Validating installation..."
python3 -m py_compile "$INSTALL_ROOT/app/chaski_web_kiosk/"*.py
systemd-analyze verify /etc/systemd/system/chaski-web-kiosk-display.service /etc/systemd/system/chaski-web-kiosk.service /etc/systemd/system/chaski-web-kiosk-control.service >/dev/null
for unit in chaski-web-kiosk-display.service chaski-web-kiosk.service chaski-web-kiosk-control.service; do
    systemctl is-enabled --quiet "$unit" || die "$unit was not enabled."
done

ip_address="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -n "$ip_address" ]] || ip_address="IP-ADDRESS"
kiosk_name="$(python3 -c 'import json; print(json.load(open("/opt/chaski-web-kiosk/config/kiosk.json"))["kiosk_name"])')"
host_name="$(hostname | tr '[:upper:]' '[:lower:]')"
control_port="$(python3 -c 'import json; print(json.load(open("/opt/chaski-web-kiosk/config/kiosk.json"))["control_port"])')"

cat <<EOF

CHASKI Web Kiosk installed successfully.

Kiosk name:
$kiosk_name

Management:
http://$ip_address:$control_port
http://$host_name.local:$control_port

Commands:
sudo systemctl status chaski-web-kiosk
sudo journalctl -u chaski-web-kiosk -f
sudo chaski-web-kiosk-status

Reboot recommended:
sudo reboot
EOF
