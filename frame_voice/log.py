"""A small log file, so problems on the headset can be diagnosed.

~/.local/state/frame-voice/frame-voice.log (kept under ~1 MB).
"""

import logging
import logging.handlers
import os
from pathlib import Path

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "frame-voice"
LOG_PATH = STATE_DIR / "frame-voice.log"

log = logging.getLogger("frame-voice")


def setup():
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=500_000, backupCount=1)
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(handler)


def tail(lines=40):
    try:
        return LOG_PATH.read_text(errors="replace").splitlines()[-lines:]
    except OSError:
        return []
