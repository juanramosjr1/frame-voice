"""fuelCell Voice Typing for the Steam Frame.

Click into any text box, tap the mic (or hold the controller button bound to
F13), speak, and your words are typed into that box.
"""

import argparse
import os
import shutil
import sys
import threading

from . import config as config_mod
from .keyboard import SHORTCUTS, Typist
from .linux_input import KeyWatcher, key_code
from .voice import Recorder, Transcriber

LOADING, READY, LISTENING, WORKING, ERROR = "loading", "ready", "listening", "working", "error"


class Engine:
    """Everything except the window: mic, speech model, typing, hotkeys.

    on_state(state, message) and on_transcript(text) are called from
    background threads.
    """

    def __init__(self, cfg, on_state=lambda s, m: None, on_transcript=lambda t: None,
                 typist_factory=Typist, recorder=None, transcriber_factory=Transcriber):
        self.cfg = cfg
        self.on_state = on_state
        self.on_transcript = on_transcript
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

    def stop_talking(self):
        with self._lock:
            if self.state != LISTENING:
                return
            path = self.recorder.stop()
            self._set(WORKING, "Typing...")
        threading.Thread(target=self._finish, args=(path,), daemon=True).start()

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
        self.last_text = text
        self.on_transcript(text)
        self._type(text)
        self._set(READY, "Ready")

    def _type(self, text):
        if self.cfg.get("add_space", True):
            text += " "
        self.typist.type_text(text)
        if self.cfg.get("press_enter"):
            self.typist.shortcut("enter")

    def retype_last(self):
        if self.last_text and self.typist and self.state == READY:
            self._type(self.last_text)

    # -- buttons ----------------------------------------------------------
    def shortcut(self, name):
        if self.typist:
            self.typist.shortcut(name)

    def hotkey(self, action, pressed):
        if action == "talk":
            # Hold to talk: press starts, release types.
            (self.start_talking if pressed else self.stop_talking)()
        elif pressed and action in SHORTCUTS:
            self.shortcut(action)


def check():
    """Print a quick health check of everything the app needs."""
    ok = True

    def line(good, text, fix=""):
        nonlocal ok
        ok &= good
        print(("  OK    " if good else "  FIX   ") + text + (f"\n        -> {fix}" if not good and fix else ""))

    print("fuelCell Voice Typing - system check\n")
    line(os.access("/dev/uinput", os.W_OK), "Can type into apps (/dev/uinput)",
         "Run install.sh again, then restart the headset.")
    cfg = config_mod.load()
    if cfg.get("keyboard_hotkeys"):
        readable = [p for p in sorted(os.listdir("/dev/input")) if p.startswith("event")
                    and os.access(f"/dev/input/{p}", os.R_OK)] if os.path.isdir("/dev/input") else []
        line(bool(readable), f"Can read keyboard hotkeys ({len(readable)} input devices)",
             "Add yourself to the 'input' group: sudo usermod -aG input $USER")
    line(bool(shutil.which("pw-record") or shutil.which("arecord")), "Microphone recorder found",
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
    print("\nAll good!" if ok else "\nSome things need fixing (see above).")
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="frame-voice", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="check the setup and exit")
    parser.add_argument("--no-intro", action="store_true", help="skip the fuelCell intro")
    parser.add_argument("--no-window", action="store_true",
                        help="background only: controller shortcuts, no window")
    parser.add_argument("--register", action="store_true",
                        help="register with SteamVR (done automatically) and exit")
    args = parser.parse_args(argv)

    if args.check:
        sys.exit(check())
    if args.register:
        from .vr import write_vrmanifest
        print(write_vrmanifest())
        return

    cfg = config_mod.load()
    if args.no_window:
        from .vr import SteamVR

        engine = Engine(cfg)
        vr = SteamVR(engine.hotkey, cfg["controller_preset"], on_status=print)
        engine.on_state = lambda s, m: (print(m), vr.show_state(s, m))
        engine.load()
        if cfg.get("keyboard_hotkeys"):
            engine.start_hotkeys()
        vr.start()
        threading.Event().wait()
        return

    from .ui import run
    sys.exit(run(cfg, Engine, show_intro=cfg["show_intro"] and not args.no_intro))


if __name__ == "__main__":
    main()
