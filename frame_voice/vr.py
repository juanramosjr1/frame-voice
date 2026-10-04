"""SteamVR integration: controller shortcuts, auto-start, and a small
"Listening..." badge in your view while you talk.

Uses SteamVR Input, so shortcuts come from the Frame controllers and can be
changed in SteamVR's own controller bindings screen.
"""

import ctypes
import json
import os
import sys
import threading
import time
from pathlib import Path

APP_KEY = "fuelcell.voicetyping"
HERE = Path(__file__).parent / "steamvr"
ACTIONS = ("talk", "copy", "paste", "select_all", "enter")

PRESETS = {
    "grip": ("Grip combos",
             "Hold grip, then press: B talk, A paste, Y copy, X select all"),
    "trigger": ("Trigger combos",
                "Hold trigger, then press: B talk, A paste, Y copy, X select all"),
    "simple": ("Simple", "Hold B to talk, hold A to paste"),
}

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "frame-voice"


def launch_command():
    exe = Path(sys.executable).with_name("frame-voice")
    return str(exe) if exe.exists() else sys.executable


def write_vrmanifest():
    """Tell SteamVR about the app so it can auto-start it and list its bindings."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "frame-voice.vrmanifest"
    exe = launch_command()
    app = {
        "app_key": APP_KEY,
        "launch_type": "binary",
        "binary_path_linux": exe,
        "arguments": "" if exe.endswith("frame-voice") else "-m frame_voice",
        "is_dashboard_overlay": True,
        "action_manifest_path": str(HERE / "actions.json"),
        "strings": {"en_us": {"name": "fuelCell Voice Typing",
                              "description": "Speak into any text box"}},
    }
    path.write_text(json.dumps({"source": "builtin", "applications": [app]}, indent=2))
    return path


class SteamVR:
    """Runs in a background thread. Never raises: if SteamVR isn't there,
    status explains why and the rest of the app keeps working."""

    def __init__(self, on_action, preset="grip", on_status=lambda text: None):
        self.on_action = on_action
        self.on_status = on_status
        self.preset = preset if preset in PRESETS else "grip"
        self.connected = False
        self._stop = False
        self._badge = None   # (state, message) to show, set from the UI thread
        self._badge_dirty = False
        self.vr = None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop = True

    def set_preset(self, preset):
        if preset in PRESETS:
            self.preset = preset

    def show_state(self, state, message=""):
        self._badge = (state, message)
        self._badge_dirty = True

    def open_bindings(self):
        if not self.connected:
            return False
        try:
            self.vr.VRInput().openBindingUI(APP_KEY, 0, 0, False)
            return True
        except Exception:
            return False

    # -- thread -------------------------------------------------------------
    def _run(self):
        try:
            import openvr
        except Exception as err:  # library missing or wrong CPU architecture
            self.on_status(f"SteamVR library unavailable ({err})")
            return
        self.vr = openvr
        while not self._stop:
            try:
                openvr.init(openvr.VRApplication_Overlay)
            except Exception:
                self.on_status("Waiting for SteamVR...")
                time.sleep(5)
                continue
            try:
                self._session(openvr)
            except Exception as err:
                self.on_status(f"SteamVR error: {err}")
            finally:
                self.connected = False
                try:
                    openvr.shutdown()
                except Exception:
                    pass
            time.sleep(3)

    def _session(self, openvr):
        apps = openvr.VRApplications()
        try:
            apps.addApplicationManifest(str(write_vrmanifest()), False)
            apps.identifyApplication(os.getpid(), APP_KEY)
            apps.setApplicationAutoLaunch(APP_KEY, True)
        except Exception:
            pass  # still works without; only auto-start and binding UI suffer
        vrinput = openvr.VRInput()
        vrinput.setActionManifestPath(str(HERE / "actions.json"))
        sets = {p: vrinput.getActionSetHandle(f"/actions/{p}") for p in PRESETS}
        handles = {p: {a: vrinput.getActionHandle(f"/actions/{p}/in/{a}") for a in ACTIONS}
                   for p in PRESETS}
        active = (openvr.VRActiveActionSet_t * 1)()
        badge = Badge(openvr)
        self.connected = True
        self.on_status("Controller shortcuts on")

        event = openvr.VREvent_t()
        held = set()
        while not self._stop:
            while openvr.VRSystem().pollNextEvent(event):
                if event.eventType == openvr.VREvent_Quit:
                    openvr.VRSystem().acknowledgeQuit_Exiting()
                    return
            preset = self.preset
            active[0].ulActionSet = sets[preset]
            # Overlay-global priority: shortcuts work over games and the Steam UI
            # (needs SteamVR > Developer > "Experimental overlay input overrides").
            active[0].nPriority = openvr.k_nActionSetOverlayGlobalPriorityMin + 0x100
            try:
                vrinput.updateActionState(active)
            except Exception:
                pass
            for action, handle in handles[preset].items():
                try:
                    data = vrinput.getDigitalActionData(handle, openvr.k_ulInvalidInputValueHandle)
                except Exception:
                    continue
                down = bool(data.bActive and data.bState)
                if down and action not in held:
                    held.add(action)
                    self.on_action(action, True)
                elif not down and action in held:
                    held.discard(action)
                    self.on_action(action, False)
            if self._badge_dirty:
                self._badge_dirty = False
                badge.show(*self._badge)
            badge.tick()
            time.sleep(1 / 60)


class Badge:
    """A small pill floating just below your view: "Listening..." / "Typing...".
    Lets you use the controller shortcuts without opening the dashboard."""

    W, H = 512, 128

    def __init__(self, openvr):
        self.openvr = openvr
        self.overlay = openvr.VROverlay()
        self.handle = None
        self.hide_at = 0.0
        try:
            self.handle = self.overlay.createOverlay(APP_KEY + ".badge", "Voice Typing")
            self.overlay.setOverlayWidthInMeters(self.handle, 0.22)
            m = openvr.HmdMatrix34_t()
            for r in range(3):
                for c in range(4):
                    m.m[r][c] = 1.0 if r == c else 0.0
            m.m[1][3] = -0.16   # a little below centre
            m.m[2][3] = -0.75   # 75 cm in front
            self.overlay.setOverlayTransformTrackedDeviceRelative(
                self.handle, openvr.k_unTrackedDeviceIndex_Hmd, m)
        except Exception:
            self.handle = None

    def show(self, state, message):
        if self.handle is None:
            return
        from .app import ERROR, LISTENING, READY, WORKING
        text = {LISTENING: "Listening...", WORKING: "Typing..."}.get(state)
        color = {LISTENING: "#ff4d5e", WORKING: "#ffb547"}.get(state, "#2ee6b8")
        if state == READY and message.startswith("Didn"):
            text, color = "Didn't catch that", "#8b94a7"
        elif state == ERROR:
            text, color = "Voice typing error", "#ff4d5e"
        if not text:
            self.hide_at = time.monotonic() + 0.4
            return
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.instance() is None:
            return  # drawing text needs the Qt app (not running with --no-window)
        try:
            buf = render_pill(text, color, self.W, self.H)
            self.overlay.setOverlayRaw(self.handle, buf, self.W, self.H, 4)
            self.overlay.showOverlay(self.handle)
        except Exception:
            return
        self.hide_at = time.monotonic() + (2.0 if state in (READY, ERROR) else 1e9)

    def tick(self):
        if self.handle is not None and self.hide_at and time.monotonic() > self.hide_at:
            self.hide_at = 0.0
            try:
                self.overlay.hideOverlay(self.handle)
            except Exception:
                pass


def render_pill(text, color, w, h):
    """RGBA pixels (ctypes buffer) of a rounded pill with a coloured dot and text."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    img = QImage(w, h, QImage.Format_RGBA8888)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(13, 16, 22, 235))
    p.drawRoundedRect(QRectF(4, 4, w - 8, h - 8), (h - 8) / 2, (h - 8) / 2)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(44, h / 2 - 16, 32, 32))
    f = QFont()
    f.setFamilies(["Inter", "Noto Sans", "Cantarell", "DejaVu Sans"])
    f.setPixelSize(46)
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    p.setPen(QColor("#eef1f6"))
    p.drawText(QRectF(100, 0, w - 130, h), Qt.AlignVCenter | Qt.AlignLeft, text)
    p.end()
    data = bytes(img.constBits())
    return (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
