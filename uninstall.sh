#!/usr/bin/env bash
set -Eeuo pipefail

readonly INSTALL_ROOT="/opt/chaski-web-kiosk"
readonly STATE_HOME="/var/lib/chaski-web-kiosk"
readonly INSTALL_STATE="$STATE_HOME/install-state"
purge=false
remove_deps=false

usage() {
    echo "Usage: sudo ./uninstall.sh [--purge] [--remove-deps]"
    echo "  --purge        Also remove configuration, media, state, and the chaski user"
    echo "  --remove-deps  Remove direct packages installed by CHASKI (never pre-existing packages)"
}

while (($#)); do
    case "$1" in
        --purge) purge=true ;;
        --remove-deps) remove_deps=true ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [[ $EUID -ne 0 ]]; then
    echo "Run this uninstaller as root: sudo ./uninstall.sh" >&2
    exit 1
fi

package_record="$INSTALL_ROOT/config/installed-packages.txt"
record_copy=""
had_install_state=false
[[ -e "$INSTALL_STATE/recorded" ]] && had_install_state=true
if $remove_deps && [[ -s "$package_record" ]]; then
    record_copy="$(mktemp)"
    cp "$package_record" "$record_copy"
fi

systemctl disable --now chaski-web-kiosk-control.service chaski-web-kiosk.service chaski-web-kiosk-display.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/chaski-web-kiosk-control.service /etc/systemd/system/chaski-web-kiosk.service /etc/systemd/system/chaski-web-kiosk-display.service
rm -f /etc/sudoers.d/chaski-web-kiosk /usr/local/bin/chaski-web-kiosk-status /usr/local/sbin/chaski-web-kiosk-update /usr/local/libexec/chaski-web-kiosk-network
systemctl daemon-reload
systemctl reset-failed >/dev/null 2>&1 || true

if [[ -f "$INSTALL_STATE/default-target" ]]; then
    original_target="$(head -n 1 "$INSTALL_STATE/default-target")"
    [[ "$original_target" =~ ^[A-Za-z0-9@_.-]+\.target$ ]] && systemctl set-default "$original_target" >/dev/null
fi
if grep -qx enabled "$INSTALL_STATE/getty-state" 2>/dev/null; then
    systemctl enable --now getty@tty1.service >/dev/null 2>&1 || true
fi
if ! grep -qx enabled "$INSTALL_STATE/avahi-state" 2>/dev/null; then
    systemctl disable --now avahi-daemon.service >/dev/null 2>&1 || true
fi
if [[ -e "$INSTALL_STATE/xwrapper-existed" && -f "$INSTALL_STATE/Xwrapper.config" ]]; then
    cp -a "$INSTALL_STATE/Xwrapper.config" /etc/X11/Xwrapper.config
elif [[ -e "$INSTALL_STATE/recorded" ]]; then
    rm -f /etc/X11/Xwrapper.config
fi

rm -rf "$INSTALL_ROOT/app" "$INSTALL_ROOT/static" "$INSTALL_ROOT/bin"
rm -f "$INSTALL_ROOT/VERSION"

if $purge; then
    rm -rf "$INSTALL_ROOT/config" "$INSTALL_ROOT/media" "$INSTALL_ROOT/source" "$STATE_HOME"
    rmdir "$INSTALL_ROOT" 2>/dev/null || true
    userdel chaski 2>/dev/null || true
    echo "CHASKI Web Kiosk completely removed."
else
    echo "CHASKI Web Kiosk removed; configuration and media preserved in $INSTALL_ROOT."
fi

if $remove_deps && [[ -n "$record_copy" ]]; then
    mapfile -t packages < "$record_copy"
    rm -f "$record_copy"
    if ((${#packages[@]})); then
        apt-get remove -y "${packages[@]}"
        echo "Removed packages originally installed by CHASKI. Dependencies were not auto-removed."
    fi
fi

if ! $had_install_state; then
    echo "No saved pre-install boot state was found. If needed, restore console boot with:"
    echo "  sudo systemctl enable --now getty@tty1.service"
    echo "  sudo systemctl set-default multi-user.target"
else
    echo "Restored the boot target, tty1 getty, and X wrapper settings recorded at first install."
fi
