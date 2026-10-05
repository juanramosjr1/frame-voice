"""Start the app with SteamVR.

SteamOS on the Steam Frame runs SteamVR as the user service steamvr.service.
A user service of ours that SteamVR "wants" starts and stops with it (the
way Frametop's helpers do) and runs the controller shortcuts without a window
(service.py). That's more reliable than SteamVR's own auto-launch list, which
is also set (in vr.py) for systems without that service; if both start the
app, the second copy just exits.

A terminal in the Frame's desktop has its own XDG_RUNTIME_DIR and D-Bus, from
which the user service manager can't be reached, so systemctl always gets the
real ones.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .log import log

UNIT = "frame-voice.service"
UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"


def launch_command():
    exe = Path(sys.executable).with_name("frame-voice")
    return f'"{exe}"' if exe.exists() else f'"{sys.executable}" -m frame_voice'


def unit_text(command):
    return f"""[Unit]
Description=fuelCell Voice Typing: speak into any text box
# Starts and stops with SteamVR, which SteamOS runs as this user service.
After=steamvr.service
PartOf=steamvr.service
Requisite=steamvr.service

[Service]
ExecStart={command} --background
Restart=on-failure
RestartSec=5

[Install]
WantedBy=steamvr.service
"""


def systemctl(*args):
    """Run systemctl --user; returns the CompletedProcess, or None if it can't run."""
    tool = shutil.which("systemctl")
    if not tool:
        return None
    env = dict(os.environ)
    runtime = Path(f"/run/user/{os.getuid()}")
    if (runtime / "bus").exists():
        env["XDG_RUNTIME_DIR"] = str(runtime)
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime / 'bus'}"
    try:
        return subprocess.run([tool, "--user", *args], capture_output=True, text=True,
                              timeout=15, env=env)
    except (OSError, subprocess.SubprocessError):
        return None


def available():
    """True where SteamVR runs as a user service (the Steam Frame)."""
    out = systemctl("cat", "steamvr.service")
    return out is not None and out.returncode == 0


def enabled():
    out = systemctl("is-enabled", UNIT)
    return out is not None and out.stdout.strip() == "enabled"


def start_now():
    """Start the background copy now, if it's set up (it needs SteamVR
    running). True if it started."""
    if not enabled():
        return False
    out = systemctl("start", UNIT)
    return out is not None and out.returncode == 0


def set_enabled(on, command=None):
    """Turn starting with SteamVR on or off. Returns True if the service is set up."""
    if not available():
        return False
    if on:
        path = UNIT_DIR / UNIT
        text = unit_text(command or launch_command())
        try:
            if not path.exists() or path.read_text() != text:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                systemctl("daemon-reload")
        except OSError as err:
            log.warning("couldn't write %s: %s", path, err)
            return False
    out = systemctl("enable" if on else "disable", UNIT)
    ok = out is not None and out.returncode == 0
    if ok:
        log.info("start with SteamVR: %s", "on" if on else "off")
    else:
        log.warning("systemctl %s %s failed: %s", "enable" if on else "disable", UNIT,
                    out.stderr.strip()[:200] if out else "no systemctl")
    return ok
