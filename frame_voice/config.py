"""Settings, saved to ~/.config/frame-voice/config.json."""

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "frame-voice"
AUTOSTART_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "frame-voice.desktop"

MODELS = {
    "tiny.en": "Fast",
    "base.en": "Balanced",
    "small.en": "Most accurate",
}

PRESET_KEYS = ("ab", "trigger", "hold_b")

DEFAULTS = {
    "model": "base.en",
    "add_space": True,
    "press_enter": False,
    "show_intro": True,
    "controller_preset": "ab",
    # "type": type the words into the focused text box (they also go on the
    # clipboard). "clipboard": only put them on the clipboard.
    "after_talking": "type",
    # Optional fallback: keyboard keys (e.g. sent by Steam Input) as shortcuts.
    "keyboard_hotkeys": False,
    "hotkeys": {
        "talk": "F13",
        "copy": "F14",
        "paste": "F15",
        "select_all": "F16",
        "enter": "F17",
        "cut": "F18",
    },
}


def load(path=None):
    path = Path(path or CONFIG_DIR / "config.json")
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        return cfg
    for key, value in saved.items():
        if key == "hotkeys" and isinstance(value, dict):
            cfg["hotkeys"].update(value)
        elif key in cfg:
            cfg[key] = value
    if cfg["model"] not in MODELS:
        cfg["model"] = DEFAULTS["model"]
    # Older versions had a "grip" layout; it was replaced by "Hold A + B".
    if cfg["controller_preset"] not in PRESET_KEYS:
        cfg["controller_preset"] = DEFAULTS["controller_preset"]
    if cfg["after_talking"] not in ("type", "clipboard"):
        cfg["after_talking"] = DEFAULTS["after_talking"]
    return cfg


def save(cfg, path=None):
    path = Path(path or CONFIG_DIR / "config.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2))


def autostart_enabled():
    return AUTOSTART_FILE.exists()


def set_autostart(enabled, exec_line):
    if enabled:
        AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART_FILE.write_text(
            "[Desktop Entry]\nType=Application\nName=Voice Typing\n"
            f"Exec={exec_line}\nIcon=frame-voice\nX-GNOME-Autostart-enabled=true\n"
        )
    elif AUTOSTART_FILE.exists():
        AUTOSTART_FILE.unlink()
