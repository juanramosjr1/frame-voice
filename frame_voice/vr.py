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

from .log import STATE_DIR, log

APP_KEY = "fuelcell.voicetyping"
HERE = Path(__file__).parent / "steamvr"
ACTIONS = ("talk", "copy", "paste", "select_all", "enter")
EDIT_HINT = "Trigger + A pastes, Trigger + Y copies, Trigger + X selects all."

PRESETS = {
    "ab": ("Hold A + B", "Hold A and B together to talk, let go to type it. " + EDIT_HINT),
    "trigger": ("Trigger + B", "Hold trigger and B to talk, let go to type it. " + EDIT_HINT),
    "hold_b": ("Hold B", "Hold B to talk, let go to type it. " + EDIT_HINT),
}

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "frame-voice"
DIAG_PATH = STATE_DIR / "steamvr.json"

# Legacy button bits (IVRSystem::GetControllerState), used only if SteamVR
# hasn't loaded any bindings for us. B is "application menu" on most
# controllers with A/B buttons.
BTN_B, BTN_A, BTN_TRIGGER = 1, 7, 33


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


def legacy_actions(preset, pressed_by_hand):
    """Which actions are held, from raw button bits per hand ('left'/'right')."""
    def on(hand, bit):
        return bool(pressed_by_hand.get(hand, 0) >> bit & 1)

    held = set()
    for hand in ("right", "left"):
        a, b, trig = on(hand, BTN_A), on(hand, BTN_B), on(hand, BTN_TRIGGER)
        if (preset == "ab" and a and b) or (preset == "trigger" and trig and b) \
                or (preset == "hold_b" and b and not trig):
            held.add("talk")
        if trig and a and not b:
            held.add("paste")
    return held


class SteamVR:
    """Runs in a background thread. Never raises: if SteamVR isn't there,
    status explains why and the rest of the app keeps working."""

    def __init__(self, on_action, preset="ab", on_status=lambda text: None):
        self.on_action = on_action
        self.on_status = on_status
        self.preset = preset if preset in PRESETS else "ab"
        self.connected = False
        self._stop = False
        self._badge = None   # (state, message) to show, set from the UI thread
        self._badge_dirty = False
        self.vr = None
        self.held = set()
        self.bound = {}
        self.controllers = []
        self.source = None
        self.summary = "Connecting to SteamVR..."

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
        except Exception as err:
            log.warning("openBindingUI failed: %s", err)
            return False

    def snapshot(self):
        return {"summary": self.summary, "connected": self.connected, "held": set(self.held),
                "bound": dict(self.bound), "controllers": list(self.controllers),
                "source": self.source, "preset": self.preset}

    def _status(self, text, summary=None):
        if summary is not None:
            self.summary = summary
        self.on_status(text)
        log.info("steamvr: %s", summary or text)

    # -- thread -------------------------------------------------------------
    def _run(self):
        try:
            import openvr
        except Exception as err:  # library missing or wrong CPU architecture
            self._status("SteamVR library unavailable",
                         f"The SteamVR library didn't load ({err}). Run the installer again.")
            return
        self.vr = openvr
        waiting_logged = False
        while not self._stop:
            try:
                openvr.init(openvr.VRApplication_Overlay)
            except Exception as err:
                if not waiting_logged:
                    log.info("steamvr init failed: %s", err)
                    waiting_logged = True
                self.connected = False
                self.on_status("Waiting for SteamVR...")
                self.summary = "Waiting for SteamVR. Shortcuts start when SteamVR is running."
                time.sleep(5)
                continue
            waiting_logged = False
            try:
                self._session(openvr)
            except Exception as err:
                log.exception("steamvr session error")
                self._status(f"SteamVR error: {err}", f"SteamVR error: {err}")
            finally:
                self.connected = False
                self.held.clear()
                try:
                    openvr.shutdown()
                except Exception:
                    pass
            time.sleep(3)

    def _register(self, openvr):
        apps = openvr.VRApplications()
        for step, call in (
            ("addApplicationManifest", lambda: apps.addApplicationManifest(str(write_vrmanifest()), False)),
            ("identifyApplication", lambda: apps.identifyApplication(os.getpid(), APP_KEY)),
            ("setApplicationAutoLaunch", lambda: apps.setApplicationAutoLaunch(APP_KEY, True)),
        ):
            try:
                call()
            except Exception as err:
                log.warning("steamvr %s failed: %s", step, err)

    def _find_controllers(self, openvr):
        system = openvr.VRSystem()
        found = {}
        for i in range(openvr.k_unMaxTrackedDeviceCount):
            try:
                if system.getTrackedDeviceClass(i) != openvr.TrackedDeviceClass_Controller:
                    continue
                role = system.getControllerRoleForTrackedDeviceIndex(i)
                kind = system.getStringTrackedDeviceProperty(i, openvr.Prop_ControllerType_String)
            except Exception:
                continue
            hand = {openvr.TrackedControllerRole_LeftHand: "left",
                    openvr.TrackedControllerRole_RightHand: "right"}.get(role, f"device{i}")
            found[hand] = (i, kind)
        return found

    def _session(self, openvr):
        self._register(openvr)
        vrinput = openvr.VRInput()
        vrinput.setActionManifestPath(str(HERE / "actions.json"))
        sets = {p: vrinput.getActionSetHandle(f"/actions/{p}") for p in PRESETS}
        handles = {p: {a: vrinput.getActionHandle(f"/actions/{p}/in/{a}") for a in ACTIONS}
                   for p in PRESETS}
        active = (openvr.VRActiveActionSet_t * 1)()
        badge = Badge(openvr)
        self.connected = True
        log.info("steamvr connected; action manifest %s", HERE / "actions.json")

        event = openvr.VREvent_t()
        controllers = {}
        next_scan = 0.0
        last_diag = None
        while not self._stop:
            while openvr.VRSystem().pollNextEvent(event):
                if event.eventType == openvr.VREvent_Quit:
                    openvr.VRSystem().acknowledgeQuit_Exiting()
                    return
                if event.eventType in (openvr.VREvent_Input_BindingLoadFailed,
                                       openvr.VREvent_Input_BindingLoadSuccessful,
                                       openvr.VREvent_ActionBindingReloaded):
                    log.info("steamvr binding event %d", event.eventType)
            now = time.monotonic()
            if now >= next_scan:
                controllers = self._find_controllers(openvr)
                self.controllers = [f"{kind} ({hand})" for hand, (_, kind) in sorted(controllers.items())]
                next_scan = now + 2.0

            preset = self.preset
            active[0].ulActionSet = sets[preset]
            # Overlay-global priority: shortcuts work over other apps and the
            # Steam UI (needs SteamVR > Developer > "Experimental overlay
            # input overrides").
            active[0].nPriority = openvr.k_nActionSetOverlayGlobalPriorityMin + 0x100
            try:
                vrinput.updateActionState(active)
            except Exception as err:
                log.debug("updateActionState: %s", err)

            held, bound = set(), {}
            for action, handle in handles[preset].items():
                try:
                    data = vrinput.getDigitalActionData(handle, openvr.k_ulInvalidInputValueHandle)
                except Exception:
                    bound[action] = False
                    continue
                bound[action] = bool(data.bActive)
                if data.bActive and data.bState:
                    held.add(action)

            if any(bound.values()):
                self.source = "SteamVR Input"
            else:
                # No bindings loaded for us: read the raw buttons instead.
                self.source = "basic buttons"
                bits = {}
                for hand, (index, _) in controllers.items():
                    try:
                        ok, state = openvr.VRSystem().getControllerState(index)
                    except Exception:
                        continue
                    if ok:
                        bits[hand] = state.ulButtonPressed
                held = legacy_actions(preset, bits)
                bound = {"talk": bool(controllers), "paste": bool(controllers)}

            for action in held - self.held:
                self.on_action(action, True)
            for action in self.held - held:
                self.on_action(action, False)
            self.held = held
            self.bound = bound

            diag = (tuple(self.controllers), self.source, tuple(sorted(a for a, b in bound.items() if b)))
            if diag != last_diag:
                last_diag = diag
                self._report(diag)

            if self._badge_dirty:
                self._badge_dirty = False
                badge.show(*self._badge)
            badge.tick()
            time.sleep(1 / 60)

    def _report(self, diag):
        controllers, source, bound = diag
        if not controllers:
            summary = "Connected to SteamVR. Turn on your controllers."
            status = "Connected to SteamVR"
        elif source == "SteamVR Input":
            summary = (f"Connected: {', '.join(controllers)}. "
                       "Press a shortcut and watch it light up.")
            status = "Controller shortcuts on"
        else:
            summary = (f"Connected: {', '.join(controllers)}. SteamVR hasn't loaded button "
                       "bindings for this app, so only talk and paste work, using basic "
                       "buttons. Try 'Customize buttons in SteamVR'.")
            status = "Controller shortcuts on (basic)"
        self._status(status, summary)
        try:
            DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)
            DIAG_PATH.write_text(json.dumps({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"), "controllers": list(controllers),
                "source": source, "bound": list(bound), "preset": self.preset,
                "summary": summary}, indent=2))
        except OSError:
            pass


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
        elif state == READY and message.startswith("Copied"):
            text, color = "Copied. Paste it", "#2ee6b8"
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
