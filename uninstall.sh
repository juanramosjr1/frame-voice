#!/usr/bin/env bash
# Removes fuelCell Voice Typing. Your settings are kept unless you pass --all.
set -uo pipefail
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"

pkill -f "frame-voice/venv/bin/frame-voice" 2>/dev/null
rm -rf "$DATA/frame-voice"
rm -f "$HOME/.local/bin/frame-voice" "$DATA/applications/frame-voice.desktop" \
      "$DATA/icons/hicolor/scalable/apps/frame-voice.svg" "$CONF/autostart/frame-voice.desktop"
[[ "${1:-}" == "--all" ]] && rm -rf "$CONF/frame-voice"
if [[ -f /etc/udev/rules.d/80-frame-voice.rules ]]; then
  echo "Removing the typing permission (needs your password)..."
  sudo rm -f /etc/udev/rules.d/80-frame-voice.rules
fi
echo "fuelCell Voice Typing removed."
