"""SteamVR integration: controller shortcuts, auto-start, and a small
"Listening..." badge in your view while you talk.

Each button the app uses (A, B, X, Y and the trigger) is its own SteamVR
action set, the way other Steam Frame overlay apps read buttons. That lets us
take only the buttons we need right now: A, B, X and Y while the app runs,
and the trigger (the laser's click) only while one of those is held. Combos
like "hold A + B" are worked out here, not by SteamVR.

An overlay app only gets buttons while something else has focus (the Steam UI,
the desktop) if SteamVR's developer setting "Enable global input from
overlays" is on. The app checks it and can turn it on with one tap.

Two read-only sources add to SteamVR Input: SteamVR's own binding page
(vrws.py, the Frame's controllers only) and the older controller-state API.
They take nothing from other apps, so with them the trigger never has to be
taken at all, and the shortcuts still work if the setting is off.
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
BUTTONS = ("a", "b", "x", "y", "trigger")
BUTTON_LABELS = {"a": "A", "b": "B", "x": "X", "y": "Y", "trigger": "Trigger"}

# What you hold to talk, per layout.
TALK = {"ab": {"a", "b"}, "trigger": {"trigger", "b"}, "hold_b": {"b"}}
# Trigger + one of these runs an edit shortcut.
EDITS = {"a": "paste", "y": "copy", "x": "select_all"}
EDIT_HINT = "Trigger + A pastes, Trigger + Y copies, Trigger + X selects all."

PRESETS = {
    "ab": ("Hold A + B", "Hold A and B together to talk. Let go to type it."),
    "trigger": ("Trigger + B", "Hold the trigger and B to talk. Let go to type it."),
    "hold_b": ("Hold B", "Hold B to talk. Let go to type it."),
}

GLOBAL_SETTING = ("steamvr", "globalActionSetPriority")
GLOBAL_SETTING_NAME = "Enable global input from overlays"

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "frame-voice"
DIAG_PATH = STATE_DIR / "steamvr.json"

# Legacy button bits (IVRSystem::GetControllerState) on the right controller.
# SteamVR may not report these to apps that use SteamVR Input; when it does,
# they're a read-only extra source that takes nothing from other apps.
LEGACY_BITS = {"b": 1, "a": 7, "trigger": 33}


def preset_hint(preset, edits=True):
    hint = PRESETS.get(preset, PRESETS["ab"])[1]
    return f"{hint} {EDIT_HINT}" if edits else hint


def buttons_to_take(preset, edits):
    """Face buttons the app uses with this layout (never the trigger)."""
    face = TALK[preset] - {"trigger"}
    if edits:
        face |= set(EDITS)
    return face


def trigger_partners(preset, edits):
    """Buttons that make a combo with the trigger. The trigger is only taken
    while one of these is held, so the laser keeps working otherwise."""
    partners = {"b"} if preset == "trigger" else set()
    if edits:
        partners |= set(EDITS)
    return partners


def combo_actions(preset, edits, held):
    """Which actions are on, from the set of held buttons."""
    if TALK[preset] <= held:
        return {"talk"}
    if edits and "trigger" in held:
        return {action for button, action in EDITS.items() if button in held}
    return set()


# Scene apps that are a home environment, not a game.
HOME_KEYS = ("steam.app.250820",)  # SteamVR Home


def is_home(app_key):
    return app_key in HOME_KEYS or app_key.startswith(("system.", "openvr.tool.", "openvr.component."))


def legacy_buttons(bits):
    return {name for name, bit in LEGACY_BITS.items() if bits >> bit & 1}


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
        # Started by SteamVR: run in the background, window hidden until opened.
        "arguments": "--background" if exe.endswith("frame-voice") else "-m frame_voice --background",
        "is_dashboard_overlay": True,
        "action_manifest_path": str(HERE / "actions.json"),
        "strings": {"en_us": {"name": "fuelCell Voice Typing",
                              "description": "Speak into any text box"}},
    }
    path.write_text(json.dumps({"source": "builtin", "applications": [app]}, indent=2))
    return path


class SteamVR:
    """Runs in a background thread. Never raises: if SteamVR isn't there,
    the status explains why and the rest of the app keeps working."""

    # SteamVR Input can take a moment to load bindings after we connect.
    BINDING_GRACE = 4.0

    def __init__(self, on_action, preset="ab", on_status=lambda text: None,
                 edits=True, in_games=False, autolaunch=True, reader=None):
        self.on_action = on_action
        self.reader = reader  # read-only buttons (vrws.ButtonReader); False: none
        self.on_status = on_status
        self.preset = preset if preset in PRESETS else "ab"
        self.edits = edits
        self.in_games = in_games
        self.autolaunch = autolaunch
        self.connected = False
        self._stop = False
        # Set while the app's other copy (the one with the window) has the
        # controllers: stay off SteamVR so nothing is typed twice.
        self.hold = False
        self._badge = None   # (state, message) to show, set from the UI thread
        self._badge_dirty = False
        self._requests = []  # functions to run on the SteamVR thread
        self._warned = set()
        self.vr = None
        self.badge = None
        self.reset()

    def reset(self):
        self.buttons = set()    # buttons held right now
        self.held = set()       # actions on right now
        self.bound = {}         # button -> SteamVR has a binding for it
        self.taken = set()      # buttons we're reading this frame
        self.controllers = []
        self.global_input = None    # the SteamVR setting (None = unknown)
        self.global_rejected = False
        self.in_game = False
        self.dashboard = False
        self.paused = False
        self.source = None
        self.status = "waiting"
        self.summary = "Connecting to SteamVR..."
        self._legacy_seen = False
        self._scene_pid = 0
        self._scene_key = ""
        self._connected_at = 0.0
        self._next_slow = 0.0
        self._last_diag = None

    # -- called from the UI thread -------------------------------------------
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop = True

    def set_preset(self, preset):
        if preset in PRESETS:
            self.preset = preset

    def set_options(self, edits=None, in_games=None):
        if edits is not None:
            self.edits = edits
        if in_games is not None:
            self.in_games = in_games

    def set_autolaunch(self, on):
        self.autolaunch = on
        self._requests.append(lambda vr: vr.VRApplications().setApplicationAutoLaunch(APP_KEY, on))

    def enable_global_input(self):
        """Turn on SteamVR's "Enable global input from overlays" setting."""
        self._requests.append(self._enable_global)

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
        return {"summary": self.summary, "status": self.status, "connected": self.connected,
                "buttons": set(self.buttons), "held": set(self.held), "bound": dict(self.bound),
                "taken": set(self.taken), "controllers": list(self.controllers),
                "source": self.source, "preset": self.preset, "global_input": self.global_input,
                "read_only": bool(self.reader and self.reader.connected)}

    # -- thread ---------------------------------------------------------------
    def _set_status(self, status, summary):
        changed = (status, summary) != (self.status, self.summary)
        self.status, self.summary = status, summary
        if changed:
            log.info("steamvr: %s", summary)
            self.on_status(summary)

    def _warn_once(self, text):
        if text not in self._warned:
            self._warned.add(text)
            log.warning("steamvr: %s", text)

    def _run(self):
        try:
            import openvr
        except Exception as err:  # library missing or wrong CPU architecture
            self._set_status("off", f"The SteamVR library didn't load ({err}). "
                                    "Run the installer again.")
            return
        self.vr = openvr
        waiting_logged = False
        while not self._stop:
            if self.hold:
                if self.status != "held":
                    self._release_all()
                    self._set_status("held", "Paused while the app's window has the shortcuts (or the app is starting).")
                time.sleep(0.2)
                continue
            try:
                # "Background" never starts SteamVR; it just fails if it isn't running.
                openvr.init(openvr.VRApplication_Background)
                openvr.shutdown()
                openvr.init(openvr.VRApplication_Overlay)
            except Exception as err:
                if not waiting_logged:
                    log.info("steamvr init failed: %s", err)
                    waiting_logged = True
                self.connected = False
                self._read_only_wait(5.0)
                continue
            waiting_logged = False
            try:
                self.open(openvr)
                while not self._stop and not self.hold and self.step():
                    time.sleep(1 / 60)
            except Exception as err:
                log.exception("steamvr session error")
                self._set_status("error", f"SteamVR error: {err}")
            finally:
                self._release_all()
                self.connected = False
                try:
                    openvr.shutdown()
                except Exception:
                    pass
            if not self.hold:
                time.sleep(3)

    def _start_reader(self):
        if self.reader is None:
            from .vrws import ButtonReader
            self.reader = ButtonReader()
        if self.reader:
            self.reader.start()

    def _read_only_wait(self, seconds):
        """While we can't connect to SteamVR (it isn't running, or this
        session can't reach it), the read-only source can still run the
        shortcuts. It works from any session, since it's a local web socket."""
        self._start_reader()
        end = time.monotonic() + seconds
        while not self._stop and not self.hold and time.monotonic() < end:
            read_only = bool(self.reader and self.reader.connected)
            self._emit(self.reader.buttons() if read_only else set())
            if read_only:
                self._set_status("readonly", "Shortcuts work through SteamVR's controller "
                                             "stream, but the buttons also reach other apps.")
            else:
                self._set_status("waiting", "Waiting for SteamVR. Shortcuts start when SteamVR runs.")
            time.sleep(1 / 60)

    def _emit(self, buttons):
        """Report combo changes for the buttons held now."""
        held = combo_actions(self.preset, self.edits, buttons)
        for action in held - self.held:
            self.on_action(action, True)
        for action in self.held - held:
            self.on_action(action, False)
        self.buttons, self.held = buttons, held

    def _release_all(self):
        for action in self.held:
            self.on_action(action, False)
        self.reset()

    def _register(self, openvr):
        apps = openvr.VRApplications()
        for step, call in (
            ("addApplicationManifest", lambda: apps.addApplicationManifest(str(write_vrmanifest()), False)),
            ("identifyApplication", lambda: apps.identifyApplication(os.getpid(), APP_KEY)),
            ("setApplicationAutoLaunch", lambda: apps.setApplicationAutoLaunch(APP_KEY, self.autolaunch)),
        ):
            try:
                call()
            except Exception as err:
                log.warning("steamvr %s failed: %s", step, err)

    def open(self, openvr):
        """Connect to SteamVR Input. Separate from step() so tests can drive it."""
        self.vr = openvr
        self._release_all()
        self._register(openvr)
        vrinput = openvr.VRInput()
        vrinput.setActionManifestPath(str(HERE / "actions.json"))
        self.sets = {b: vrinput.getActionSetHandle(f"/actions/{b}") for b in BUTTONS}
        self.actions = {b: vrinput.getActionHandle(f"/actions/{b}/in/press") for b in BUTTONS}
        self.global_priority = openvr.k_nActionSetOverlayGlobalPriorityMin + 0x100
        self.event = openvr.VREvent_t()
        self._start_reader()
        self.badge = Badge(openvr)
        self.connected = True
        self._connected_at = time.monotonic()
        log.info("steamvr connected; action manifest %s", HERE / "actions.json")

    def _events(self, openvr):
        """Handle SteamVR events. Returns False when SteamVR is quitting."""
        names = {getattr(openvr, n, None): n for n in (
            "VREvent_Input_BindingLoadFailed", "VREvent_Input_BindingLoadSuccessful",
            "VREvent_Input_ActionManifestReloaded", "VREvent_Input_ActionManifestLoadFailed",
            "VREvent_Input_BindingsUpdated", "VREvent_ActionBindingReloaded")}
        system = openvr.VRSystem()
        while system.pollNextEvent(self.event):
            kind = self.event.eventType
            if kind == openvr.VREvent_Quit:
                system.acknowledgeQuit_Exiting()
                return False
            if kind in names:
                log.info("steamvr event: %s", names[kind])
        return True

    def _find_controllers(self, openvr):
        system = openvr.VRSystem()
        found = []
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
            found.append((hand, i, kind))
        return sorted(found)

    def _slow_checks(self, openvr):
        """Once a second: controllers, the global input setting, games, dashboard."""
        self._devices = self._find_controllers(openvr)
        self.controllers = [f"{kind} ({hand})" for hand, _, kind in self._devices]
        try:
            setting = bool(openvr.VRSettings().getBool(*GLOBAL_SETTING))
        except Exception as err:  # an unset setting reads as an error: it's off
            log.debug("reading %s: %r", GLOBAL_SETTING, err)
            setting = False
        if setting != self.global_input:
            log.info("steamvr: '%s' is %s", GLOBAL_SETTING_NAME, "on" if setting else "off")
            if setting:
                self.global_rejected = False  # turned on since: try again
        self.global_input = setting
        try:
            pid = openvr.VRApplications().getCurrentSceneProcessId()
        except Exception:
            pid = 0
        if pid != self._scene_pid:
            self._scene_pid = pid
            try:
                key = openvr.VRApplications().getApplicationKeyByProcessId(pid) if pid else ""
            except Exception:
                key = "?"
            log.info("steamvr: scene app is now %s", f"{key} (pid {pid})" if pid else "none")
            self._scene_key = key
        self.in_game = bool(pid) and pid != os.getpid() and not is_home(self._scene_key)
        try:
            dashboard = bool(openvr.VROverlay().isDashboardVisible())
        except Exception:
            dashboard = False
        if dashboard != self.dashboard:
            log.info("steamvr: dashboard %s", "open" if dashboard else "closed")
        self.dashboard = dashboard

    def _legacy(self, openvr):
        """Right-controller buttons from the older, read-only API."""
        for hand, index, _ in getattr(self, "_devices", ()):
            if hand != "right":
                continue
            try:
                ok, state = openvr.VRSystem().getControllerState(index)
            except Exception:
                return set()
            if ok:
                return legacy_buttons(state.ulButtonPressed)
        return set()

    def _enable_global(self, openvr):
        try:
            openvr.VRSettings().setBool(*GLOBAL_SETTING, True)
            log.info("turned on SteamVR '%s'", GLOBAL_SETTING_NAME)
        except Exception as err:
            log.warning("couldn't turn on %s: %r", GLOBAL_SETTING, err)
        self.global_rejected = False
        self._next_slow = 0.0

    def step(self):
        """One frame: read the buttons we need, report combos. Returns False
        when SteamVR is quitting."""
        openvr = self.vr
        if not self._events(openvr):
            return False
        while self._requests:
            try:
                self._requests.pop(0)(openvr)
            except Exception as err:
                log.warning("steamvr request failed: %r", err)
        now = time.monotonic()
        if now >= self._next_slow:
            self._slow_checks(openvr)
            self._next_slow = now + 1.0

        preset, edits = self.preset, self.edits
        # Leave the buttons to a game unless asked not to. With the Steam menu
        # open over a game, the buttons are ours again.
        self.paused = self.in_game and not self.dashboard and not self.in_games
        read_only = bool(self.reader and self.reader.connected)
        take = buttons_to_take(preset, edits)
        if not (self.paused or read_only) and (
                self.buttons & trigger_partners(preset, edits)
                # keep it until it's let go, or the laser would get half a click
                or ("trigger" in self.taken and "trigger" in self.buttons)):
            take.add("trigger")  # (the read-only source reports it without taking it)
        global_ok = not (self.paused or self.global_rejected)
        priority = self.global_priority if global_ok else 0

        active = (openvr.VRActiveActionSet_t * len(take))()
        for slot, button in zip(active, sorted(take)):
            slot.ulActionSet = self.sets[button]
            slot.ulRestrictedToDevice = openvr.k_ulInvalidInputValueHandle
            slot.nPriority = priority
        try:
            openvr.VRInput().updateActionState(active)
        except Exception as err:
            if priority and type(err).__name__ == "InputError_InvalidPriority":
                # SteamVR refuses overlay-global priority while the setting is off.
                self.global_rejected = True
                self._warn_once("global input priority refused; is the setting off?")
            else:
                self._warn_once(f"updateActionState failed: {err!r}")

        buttons = set()
        for button in take:
            try:
                data = openvr.VRInput().getDigitalActionData(
                    self.actions[button], openvr.k_ulInvalidInputValueHandle)
            except Exception as err:
                self._warn_once(f"reading {button}: {err!r}")
                self.bound[button] = False
                continue
            self.bound[button] = bool(data.bActive)
            if data.bActive and data.bState:
                buttons.add(button)
        legacy = self._legacy(openvr)
        if legacy:
            self._legacy_seen = True
        buttons |= legacy
        if read_only:
            buttons |= self.reader.buttons()
        if self.paused:
            buttons = set()

        self._emit(buttons)
        self.taken = take

        self._report(now, read_only)
        if self._badge_dirty and self.badge:
            self._badge_dirty = False
            self.badge.show(*self._badge)
        if self.badge:
            self.badge.tick()
        return True

    def _report(self, now, read_only=False):
        if any(self.bound.values()):
            self.source = "SteamVR Input"
        elif read_only:
            self.source = "read-only"
        elif self._legacy_seen:
            self.source = "basic buttons"
        else:
            self.source = None
        settled = now - self._connected_at > self.BINDING_GRACE
        setting_off = self.global_input is False or self.global_rejected
        diag = (tuple(self.controllers), self.source, self.global_input, self.global_rejected,
                self.paused, self.preset, self.edits, settled, read_only)
        if diag == self._last_diag:
            return
        self._last_diag = diag
        if not self.controllers:
            status, summary = "connected", "Connected to SteamVR. Turn on your controllers."
        elif self.paused:
            status, summary = "paused", ("Paused while a game is running, so the game keeps its "
                                         "buttons. Open the Steam menu to use them.")
        elif setting_off and read_only:
            status, summary = "partial", (f"Shortcuts work, but the buttons also reach other apps. "
                                          f"Tap Turn on (SteamVR “{GLOBAL_SETTING_NAME}”) "
                                          "so they're only used for voice typing.")
        elif setting_off:
            status, summary = "setting", (f"Controller shortcuts need one SteamVR setting: "
                                          f"“{GLOBAL_SETTING_NAME}”. Tap Turn on.")
        elif not self.source and settled:
            status, summary = "nobind", ("SteamVR hasn't loaded button bindings for this app. "
                                         "Restart the headset, then open the app again.")
        else:
            status, summary = "on", (f"Connected: {', '.join(self.controllers)}. "
                                     "Press your buttons and watch them light up.")
        self._set_status(status, summary)
        try:
            DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)
            DIAG_PATH.write_text(json.dumps({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"), "status": status, "summary": summary,
                "controllers": self.controllers, "source": self.source,
                "bound": sorted(b for b, on in self.bound.items() if on),
                "global_input": self.global_input, "global_rejected": self.global_rejected,
                "in_game": self.in_game, "dashboard": self.dashboard, "paused": self.paused,
                "read_only_source": read_only,
                "preset": self.preset, "edit_shortcuts": self.edits}, indent=2))
        except OSError:
            pass


class Badge:
    """A small pill floating just below your view: "Listening..." / "Typing...".
    Lets you use the controller shortcuts without opening the window."""

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
            return  # drawing text needs a Qt app
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
