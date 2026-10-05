#!/usr/bin/env bash
# Removes fuelCell Voice Typing. Your settings are kept unless you pass --all.
set -uo pipefail
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"

# Stop starting with SteamVR (the real user session, even from the Frame's desktop).
rt="/run/user/$(id -u)"
usersctl() {
  if [[ -S "$rt/bus" ]]; then
    XDG_RUNTIME_DIR="$rt" DBUS_SESSION_BUS_ADDRESS="unix:path=$rt/bus" systemctl --user "$@"
  else
    systemctl --user "$@"
  fi
}
usersctl disable --now frame-voice.service >/dev/null 2>&1
rm -f "$CONF/systemd/user/frame-voice.service" "$CONF/systemd/user/steamvr.service.wants/frame-voice.service"
usersctl daemon-reload >/dev/null 2>&1
pkill -f "frame-voice/venv/bin/(frame-voice|python[0-9.]* -m frame_voice)" 2>/dev/null
# Remove the KDE window rule the app added (keeps your other rules).
"$DATA/frame-voice/venv/bin/frame-voice" --remove-kwin-rule 2>/dev/null
rm -rf "$DATA/frame-voice"
rm -f "$HOME/.local/bin/frame-voice" "$DATA/applications/frame-voice.desktop" \
      "$DATA/icons/hicolor/scalable/apps/frame-voice.svg" "$CONF/autostart/frame-voice.desktop"
[[ "${1:-}" == "--all" ]] && rm -rf "$CONF/frame-voice"
if [[ -f /etc/udev/rules.d/80-frame-voice.rules ]]; then
  echo "Removing the typing permission (needs your password)..."
  sudo rm -f /etc/udev/rules.d/80-frame-voice.rules /etc/atomic-update.conf.d/frame-voice.conf
fi
echo "fuelCell Voice Typing removed."
