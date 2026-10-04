#!/usr/bin/env bash
# fuelCell Voice Typing installer for the Steam Frame (SteamOS desktop mode).
#
# One line, in Konsole:
#   curl -fsSL https://raw.githubusercontent.com/juanramosjr1/frame-voice/main/install.sh | bash
set -euo pipefail

REPO="juanramosjr1/frame-voice"
BRANCH="${FRAME_VOICE_BRANCH:-main}"
# Valve's official Linux ARM64 OpenVR library (pinned to a known commit).
OPENVR_COMMIT="0924064316de3effbcd1acf1e309182a2deb1c05"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/frame-voice"
VENV="$APP_DIR/venv"
BIN="$HOME/.local/bin"

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '\n\033[31mInstall stopped:\033[0m %s\n' "$*"; exit 1; }

printf '\n\033[1;36m  fuelCell\033[0m Voice Typing installer\n'

# 1. Get the app ---------------------------------------------------------------
bold "1/5  Getting the app"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [[ -z "$SRC" || ! -f "$SRC/pyproject.toml" || ! -d "$SRC/frame_voice" ]]; then
  SRC="$(mktemp -d)"
  curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" \
    | tar -xz -C "$SRC" --strip-components=1 || fail "couldn't download the app. Check your internet."
fi
ok "Downloaded"

# 2. Python environment ----------------------------------------------------------
bold "2/5  Installing (a few minutes the first time)"
command -v python3 >/dev/null || fail "python3 not found."
python3 - <<'PY' || fail "Python 3.10 or newer is needed."
import sys; sys.exit(sys.version_info < (3, 10))
PY
mkdir -p "$APP_DIR" "$BIN"
[[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV" || fail "couldn't create a Python environment."
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet --upgrade "$SRC" || fail "package install failed (see above)."
ok "App installed"

if [[ "$(uname -m)" == "aarch64" ]]; then
  # The Python SteamVR package only ships x86 libraries; use Valve's ARM build.
  OVR_DIR="$("$VENV/bin/python" -c 'import importlib.util,os;print(os.path.dirname(importlib.util.find_spec("openvr").origin))')"
  curl -fsSL -o "$OVR_DIR/libopenvr_api_64.so" \
    "https://raw.githubusercontent.com/ValveSoftware/openvr/$OPENVR_COMMIT/bin/linuxarm64/libopenvr_api.so" \
    || fail "couldn't download the SteamVR library."
  ok "SteamVR library for ARM added"
fi
"$VENV/bin/python" -c "import openvr" 2>/dev/null && ok "SteamVR support ready" \
  || warn "SteamVR library didn't load; controller shortcuts won't work."

# 3. Speech model ----------------------------------------------------------------
bold "3/5  Downloading the speech model (~140 MB, one time)"
if "$VENV/bin/python" - <<'PY'
import sys
try:
    from faster_whisper import WhisperModel
    WhisperModel("base.en", device="cpu", compute_type="int8")
except Exception as err:
    print(f"  ({type(err).__name__}: {err})")
    sys.exit(1)
PY
then
  ok "Speech model ready"
else
  warn "Model download failed; the app will try again when it starts."
fi

# 4. Permission to type ----------------------------------------------------------
bold "4/5  Permission to type into apps"
if [[ -w /dev/uinput ]]; then
  ok "Already allowed"
else
  echo "  Voice typing needs to act like a keyboard. This needs your password once."
  echo "  (No password yet? Press Ctrl+C, run 'passwd' to set one, then run this again.)"
  sudo mkdir -p /etc/udev/rules.d || fail "couldn't add the permission."
  echo 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' \
    | sudo tee /etc/udev/rules.d/80-frame-voice.rules >/dev/null || fail "couldn't add the permission."
  { sudo udevadm control --reload-rules && sudo udevadm trigger --name-match=uinput; } 2>/dev/null || true
  ok "Allowed (takes effect after a restart if not right away)"
fi

# 5. Launcher ---------------------------------------------------------------------
bold "5/5  Adding it to your apps"
ln -sf "$VENV/bin/frame-voice" "$BIN/frame-voice"
cp "$SRC/uninstall.sh" "$APP_DIR/uninstall.sh"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$ICON_DIR" "${XDG_DATA_HOME:-$HOME/.local/share}/applications"
cp "$SRC/assets/frame-voice.svg" "$ICON_DIR/frame-voice.svg"
cat > "${XDG_DATA_HOME:-$HOME/.local/share}/applications/frame-voice.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=fuelCell Voice Typing
Comment=Speak into any text box, plus copy and paste buttons
Exec=$VENV/bin/frame-voice
Icon=frame-voice
Categories=Utility;Accessibility;
DESKTOP
"$VENV/bin/frame-voice" --register >/dev/null 2>&1 || true
ok "Added 'fuelCell Voice Typing' to your apps"

printf '\n\033[1;32mAll done!\033[0m Open \033[1mfuelCell Voice Typing\033[0m from your apps.\n'
printf 'Check everything with:  frame-voice --check\n\n'
