"""fuelCell Voice Typing for the Steam Frame.

Click into any text box, hold A + B on the controller (or tap the mic), speak,
let go, and your words are typed into that box.
"""

import argparse
import os
import shutil
import sys
import threading
import time

from . import config as config_mod
from .log import log
from .keyboard import SHORTCUTS, Typist
from .linux_input import KeyWatcher, key_code
from .voice import Recorder, Transcriber

LOADING, READY, LISTENING, WORKING, ERROR = "loading", "ready", "listening", "working", "error"


class Engine:
    """Everything except the window: mic, speech model, typing, hotkeys.

    on_state(state, message) and on_transcript(text) are called from
    background threads.

    before_input() runs right before any keys are sent. It moves keyboard
    focus off our own window if it has it, and returns False if it couldn't.
    Keys are sent anyway (our window ignores typed keys, so nothing breaks),
    and every dictation is on the clipboard to paste by hand if needed.
    """

    # Keep recording this long after the button is released, so the last
    # word isn't cut off.
    RELEASE_TAIL = 0.3

    def __init__(self, cfg, on_state=lambda s, m: None, on_transcript=lambda t: None,
                 typist_factory=Typist, recorder=None, transcriber_factory=Transcriber,
                 before_input=lambda: True):
        self.cfg = cfg
        self.on_state = on_state
        self.on_transcript = on_transcript
        self.before_input = before_input
        self.typist_factory = typist_factory
        self.transcriber_factory = transcriber_factory
        self.recorder = recorder or Recorder()
        self.typist = None
        self.transcriber = None
        self.state = LOADING
        self.last_text = ""
        self._lock = threading.Lock()
        self._watcher = None

    def _set(self, state, message=""):
        self.state = state
        if state == ERROR:
            log.error(message)
        self.on_state(state, message)

    # -- start-up ---------------------------------------------------------
    def load(self):
        self._set(LOADING, "Getting ready...")
        try:
            self.typist = self.typist_factory()
        except PermissionError:
            self._set(ERROR, "No permission to type. Run the installer again, then restart.")
            return
        except OSError as err:
            self._set(ERROR, f"Couldn't create the virtual keyboard ({err.strerror}).")
            return
        self._set(LOADING, "Loading speech model...")
        try:
            self.transcriber = self.transcriber_factory(self.cfg["model"])
        except Exception as err:  # model download/load can fail many ways
            self._set(ERROR, f"Couldn't load the speech model: {err}")
            return
        log.info("ready (model %s)", self.cfg["model"])
        self._set(READY, "Ready")

    def reload_model(self, model):
        self.cfg["model"] = model
        threading.Thread(target=self._reload_model, daemon=True).start()

    def _reload_model(self):
        self._set(LOADING, "Switching speech model...")
        try:
            self.transcriber = self.transcriber_factory(self.cfg["model"])
        except Exception as err:
            self._set(ERROR, f"Couldn't load the speech model: {err}")
            return
        self._set(READY, "Ready")

    def start_hotkeys(self):
        bindings = {}
        for action, key in self.cfg["hotkeys"].items():
            try:
                bindings[key_code(key)] = action
            except ValueError:
                continue
        self._watcher = KeyWatcher(bindings, lambda code, down: self.hotkey(bindings[code], down))
        threading.Thread(target=self._watcher.run, daemon=True).start()

    # -- talking ----------------------------------------------------------
    def start_talking(self):
        with self._lock:
            if self.state != READY:
                return
            try:
                self.recorder.start()
            except Exception as err:
                self._set(ERROR, f"Microphone problem: {err}")
                return
            self._set(LISTENING, "Listening...")

    def stop_talking(self, tail=None):
        with self._lock:
            if self.state != LISTENING:
                return
            self._set(WORKING, "Typing...")
        threading.Thread(target=self._stop_and_finish,
                         args=(self.RELEASE_TAIL if tail is None else tail,), daemon=True).start()

    def _stop_and_finish(self, tail):
        if tail:
            time.sleep(tail)
        self._finish(self.recorder.stop())

    def cancel_talking(self):
        with self._lock:
            if self.state == LISTENING:
                self.recorder.cancel()
                self._set(READY, "Ready")

    def toggle_talking(self):
        if self.state == LISTENING:
            self.stop_talking()
        else:
            self.start_talking()

    def _finish(self, path):
        try:
            text = self.transcriber.transcribe(path) if path else ""
        except Exception as err:
            self._set(ERROR, f"Couldn't understand the recording: {err}")
            return
        finally:
            if path and os.path.exists(path):
                os.unlink(path)
        if not text:
            self._set(READY, "Didn't catch that. Try again.")
            return
        log.info("heard %d characters", len(text))
        self.last_text = text
        self.on_transcript(text)  # the window also puts it on the clipboard
        if self.cfg.get("after_talking", "type") == "clipboard":
            self._set(READY, "Copied. Paste it where you want it.")
        elif self._type(text):
            self._set(READY, "Ready")
        else:
            self._set(READY, "Copied. If it didn't appear, click the text box and paste.")

    def _type(self, text):
        """Type text into the focused box. Returns False if our own window
        may have had the keyboard (then the words are only on the clipboard)."""
        focused = self.before_input()
        if not focused:
            log.warning("our window may have keyboard focus; typing anyway")
        if self.cfg.get("add_space", True):
            text += " "
        self.typist.type_text(text)
        if self.cfg.get("press_enter"):
            self.typist.shortcut("enter")
        return focused

    def retype_last(self):
        if self.last_text and self.typist and self.state == READY:
            threading.Thread(target=self._type, args=(self.last_text,), daemon=True).start()

    # -- buttons ----------------------------------------------------------
    def shortcut(self, name):
        if self.typist:
            self.before_input()
            self.typist.shortcut(name)

    def hotkey(self, action, pressed):
        log.info("shortcut %s %s", action, "down" if pressed else "up")
        if action == "talk":
            # Hold to talk: press starts, release types.
            (self.start_talking if pressed else self.stop_talking)()
        elif pressed and action in SHORTCUTS:
            self.shortcut(action)


def app_version():
    try:
        from importlib.metadata import version
        return version("frame-voice")
    except Exception:
        return "dev"


def session_info():
    """Which display session we're in. On the Frame, apps either run in the
    Steam (VR) session or inside the KDE desktop shown in VR, and typing and
    focus work differently in each."""
    env = {k: os.environ.get(k, "") for k in (
        "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_CURRENT_DESKTOP",
        "XDG_SESSION_TYPE", "DBUS_SESSION_BUS_ADDRESS", "QT_QPA_PLATFORM")}
    if "KDE" in env["XDG_CURRENT_DESKTOP"].upper() or env["XDG_RUNTIME_DIR"].endswith("nested_plasma"):
        env["session"] = "KDE desktop"
    elif env["DISPLAY"] or env["WAYLAND_DISPLAY"]:
        env["session"] = "not KDE (on the Frame: the Steam session)"
    else:
        env["session"] = "no display"
    return env


def _steamvr_now():
    """Ask a running SteamVR for what the shortcuts depend on. Never starts SteamVR."""
    import openvr

    from .vr import GLOBAL_SETTING, GLOBAL_SETTING_NAME

    openvr.init(openvr.VRApplication_Background)
    try:
        out = []
        try:
            on = openvr.VRSettings().getBool(*GLOBAL_SETTING)
        except Exception:
            on = False
        out.append((bool(on), f"SteamVR '{GLOBAL_SETTING_NAME}' is {'on' if on else 'off'}",
                    "Open the app and tap Turn on, or turn it on in SteamVR Settings > "
                    "Developer (show Advanced Settings first)."))
        system = openvr.VRSystem()
        kinds = []
        for i in range(openvr.k_unMaxTrackedDeviceCount):
            try:
                if system.getTrackedDeviceClass(i) == openvr.TrackedDeviceClass_Controller:
                    kinds.append(system.getStringTrackedDeviceProperty(i, openvr.Prop_ControllerType_String))
            except Exception:
                pass
        out.append((bool(kinds), f"Controllers: {', '.join(kinds) or 'none'}",
                    "Turn on your controllers."))
        pid = openvr.VRApplications().getCurrentSceneProcessId()
        out.append((True, "A VR game is running" if pid else "No VR game running", ""))
        return out
    finally:
        openvr.shutdown()


def check():
    """Print a quick health check of everything the app needs."""
    import json

    from .log import LOG_PATH, tail
    from .vr import DIAG_PATH

    ok = True

    def line(good, text, fix=""):
        nonlocal ok
        ok &= good
        print(("  OK    " if good else "  FIX   ") + text + (f"\n        -> {fix}" if not good and fix else ""))

    print(f"fuelCell Voice Typing {app_version()} - system check\n")
    line(os.access("/dev/uinput", os.W_OK), "Can type into apps (/dev/uinput)",
         "Run install.sh again, then restart the headset.")
    cfg = config_mod.load()
    if cfg.get("keyboard_hotkeys"):
        readable = [p for p in sorted(os.listdir("/dev/input")) if p.startswith("event")
                    and os.access(f"/dev/input/{p}", os.R_OK)] if os.path.isdir("/dev/input") else []
        line(bool(readable), f"Can read keyboard hotkeys ({len(readable)} input devices)",
             "Add yourself to the 'input' group: sudo usermod -aG input $USER")
    recorder = shutil.which("pw-record") or shutil.which("arecord")
    line(bool(recorder), f"Microphone recorder: {os.path.basename(recorder or 'missing')}",
         "SteamOS should include pw-record; check that PipeWire is installed.")
    try:
        from faster_whisper.utils import download_model
        download_model(cfg["model"], local_files_only=True)
        line(True, f"Speech model '{cfg['model']}' downloaded")
    except Exception:
        line(False, f"Speech model '{cfg['model']}' downloaded",
             "Open the app once while online; it downloads automatically.")
    try:
        import PySide6  # noqa: F401
        line(True, "Window toolkit (Qt) installed")
    except ImportError:
        line(False, "Window toolkit (Qt) installed", "Run install.sh again.")
    try:
        import openvr
        line(True, "SteamVR library loaded")
        line(bool(openvr.isRuntimeInstalled()), "SteamVR installed",
             "Install and run SteamVR to use controller shortcuts.")
    except Exception as err:
        line(False, "SteamVR library loaded", f"Run install.sh again ({err}).")
    else:
        try:
            if not openvr.isRuntimeInstalled():
                raise RuntimeError("SteamVR isn't installed")
            for good, text, fix in _steamvr_now():
                line(good, text, fix)
        except Exception:
            print("  --    SteamVR isn't running right now (shortcuts need it).")

    try:
        diag = json.loads(DIAG_PATH.read_text())
        print(f"\nController shortcuts (last seen by the app at {diag['time']}):\n  {diag['summary']}")
        print(f"  layout: {diag['preset']}, source: {diag['source']}, "
              f"buttons bound: {', '.join(diag['bound']) or 'none'}")
    except (OSError, ValueError, KeyError):
        print("\nController shortcuts: no SteamVR session seen yet (open the app while SteamVR runs).")
    info = session_info()
    print(f"\nSession: {info.pop('session')}")
    for key, value in info.items():
        print(f"  {key}={value}")
    print(f"  python {sys.version.split()[0]}")
    print("\nAll good!" if ok else "\nSome things need fixing (see above).")
    lines = tail(15)
    if lines:
        print(f"\nRecent log ({LOG_PATH}):")
        for entry in lines:
            print("  " + entry)
    return 0 if ok else 1


def clean_steam_env(argv):
    """Apps started by Steam or SteamVR inherit Steam's runtime libraries
    (LD_LIBRARY_PATH / LD_PRELOAD), which can break the window toolkit.
    Restart once without them."""
    dirty = [k for k in ("LD_LIBRARY_PATH", "LD_PRELOAD") if "steam" in os.environ.get(k, "").lower()]
    if not dirty or os.environ.get("FRAME_VOICE_CLEAN_ENV"):
        return
    env = {k: v for k, v in os.environ.items() if k not in dirty}
    env["FRAME_VOICE_CLEAN_ENV"] = "1"
    try:
        os.execve(sys.executable, [sys.executable, "-m", "frame_voice", *argv], env)
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(prog="frame-voice", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="check the setup and exit")
    parser.add_argument("--no-intro", action="store_true", help="skip the fuelCell intro")
    parser.add_argument("--no-window", action="store_true",
                        help="background only: controller shortcuts, no window")
    parser.add_argument("--background", action="store_true",
                        help="start with the window hidden (opening the app shows it)")
    parser.add_argument("--register", action="store_true",
                        help="register with SteamVR (done automatically) and exit")
    parser.add_argument("--remove-kwin-rule", action="store_true", help=argparse.SUPPRESS)
    argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(argv)

    from .log import setup as setup_log
    setup_log()
    if args.check:
        sys.exit(check())
    if args.register:
        from .vr import write_vrmanifest
        print(write_vrmanifest())
        return
    if args.remove_kwin_rule:
        from .focus import remove_kwin_rule
        remove_kwin_rule()
        return

    clean_steam_env(argv)
    info = session_info()
    log.info("starting %s %s; session: %s", app_version(), " ".join(argv) or "(no options)",
             ", ".join(f"{k}={v}" for k, v in info.items() if v))
    cfg = config_mod.load()
    if args.no_window:
        from .vr import SteamVR

        engine = Engine(cfg)
        vr = SteamVR(engine.hotkey, cfg["controller_preset"], on_status=print,
                     edits=cfg["edit_shortcuts"], in_games=cfg["in_games"],
                     autolaunch=cfg["autostart"])
        engine.on_state = lambda s, m: (print(m), vr.show_state(s, m))
        engine.load()
        if cfg.get("keyboard_hotkeys"):
            engine.start_hotkeys()
        vr.start()
        threading.Event().wait()
        return

    from .ui import run
    sys.exit(run(cfg, Engine, show_intro=cfg["show_intro"] and not args.no_intro,
                 background=args.background))


if __name__ == "__main__":
    main()
