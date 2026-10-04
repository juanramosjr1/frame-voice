import json
import os
import re
import struct
from pathlib import Path

import pytest

from frame_voice import config, linux_input
from frame_voice.app import ERROR, LISTENING, READY, Engine
from frame_voice.keyboard import SHORTCUTS, Typist, to_keystrokes
from frame_voice.linux_input import KEY, KeyWatcher, key_code

K = KEY


# -- key codes and kernel structs ---------------------------------------------
HEADER = Path("/usr/include/linux/input-event-codes.h")


@pytest.mark.skipif(not HEADER.exists(), reason="kernel headers not installed")
def test_key_codes_match_kernel_header():
    defs = dict(re.findall(r"#define KEY_(\w+)\s+(\d+)", HEADER.read_text()))
    for name, code in KEY.items():
        assert int(defs[name]) == code, name


def test_known_key_codes():
    assert (K["A"], K["ENTER"], K["LEFTCTRL"], K["SPACE"], K["F13"], K["F24"]) == (30, 28, 29, 57, 183, 194)
    assert key_code("KEY_F13") == key_code("f13") == 183
    with pytest.raises(ValueError):
        key_code("NOPE")


def test_uinput_ioctls_and_structs():
    assert struct.calcsize("llHHi") == 24  # input_event on 64-bit
    assert linux_input._UINPUT_SETUP.size == 92
    assert linux_input.UI_DEV_CREATE == 0x5501
    assert linux_input.UI_DEV_SETUP == 0x405C5503
    assert linux_input.UI_SET_EVBIT == 0x40045564
    assert linux_input.UI_SET_KEYBIT == 0x40045565
    assert linux_input.EVIOCGNAME(256) == 0x81004506


# -- typing ----------------------------------------------------------------------
class FakeDevice:
    def __init__(self):
        self.events = []

    def emit(self, code, value):
        self.events.append((code, value))

    def close(self):
        pass


def test_text_to_keystrokes():
    assert to_keystrokes("aB") == [(K["A"], False), (K["B"], True)]
    assert to_keystrokes("?!") == [(K["SLASH"], True), (K["1"], True)]
    assert to_keystrokes("don’t") == to_keystrokes("don't")
    assert to_keystrokes("café") == to_keystrokes("caf")


def test_typist_presses_shift_around_capitals():
    dev = FakeDevice()
    Typist(dev, delay=0).type_text("Hi")
    sh, h, i = K["LEFTSHIFT"], K["H"], K["I"]
    assert dev.events == [(sh, 1), (h, 1), (h, 0), (sh, 0), (i, 1), (i, 0)]


@pytest.mark.parametrize("name", sorted(SHORTCUTS))
def test_shortcuts_press_then_release_in_reverse(name):
    dev = FakeDevice()
    Typist(dev, delay=0).shortcut(name)
    codes = [K[k] for k in SHORTCUTS[name]]
    assert dev.events == [(c, 1) for c in codes] + [(c, 0) for c in reversed(codes)]


def test_keywatcher_parses_events_and_ignores_repeat():
    seen = []
    w = KeyWatcher({183}, lambda c, down: seen.append((c, down)))
    ev = linux_input._EVENT
    data = (ev.pack(0, 0, 1, 183, 1) + ev.pack(0, 0, 0, 0, 0) + ev.pack(0, 0, 1, 183, 2)
            + ev.pack(0, 0, 1, 30, 1) + ev.pack(0, 0, 1, 183, 0))
    w.handle(data)
    assert seen == [(183, True), (183, False)]


# -- settings --------------------------------------------------------------------
def test_config_roundtrip_and_bad_values(tmp_path):
    path = tmp_path / "c.json"
    cfg = config.load(path)
    assert cfg["model"] == "base.en" and cfg["controller_preset"] == "grip"
    cfg["press_enter"] = True
    cfg["hotkeys"]["talk"] = "F20"
    config.save(cfg, path)
    again = config.load(path)
    assert again["press_enter"] is True and again["hotkeys"]["talk"] == "F20"
    assert again["hotkeys"]["copy"] == "F14"
    path.write_text(json.dumps({"model": "huge-thing", "junk": 1}))
    assert config.load(path)["model"] == "base.en"
    path.write_text("{not json")
    assert config.load(path)["model"] == "base.en"


# -- engine ----------------------------------------------------------------------
class FakeRecorder:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.recording = False

    def start(self):
        self.recording = True

    def stop(self):
        self.recording = False
        p = self.tmp / "rec.wav"
        p.write_bytes(b"RIFF")
        return str(p)

    def cancel(self):
        self.recording = False


def make_engine(tmp_path, text="hello there", **cfg_over):
    cfg = config.load(tmp_path / "none.json")
    cfg.update(cfg_over)
    dev = FakeDevice()
    states = []

    class Tx:
        def __init__(self, model):
            self.model = model

        def transcribe(self, path):
            assert os.path.exists(path)
            return text

    eng = Engine(cfg, on_state=lambda s, m: states.append(s),
                 typist_factory=lambda: Typist(dev, delay=0),
                 recorder=FakeRecorder(tmp_path), transcriber_factory=Tx)
    eng.load()
    return eng, dev, states


def typed(dev):
    inv = {v: k for k, v in K.items()}
    return [inv[c] for c, v in dev.events if v == 1 and inv[c] != "LEFTSHIFT"]


def test_hold_to_talk_types_and_cleans_up(tmp_path):
    eng, dev, states = make_engine(tmp_path, "ok go")
    eng.hotkey("talk", True)
    assert eng.state == LISTENING
    path = eng.recorder.stop()
    eng.state = "working"
    eng._finish(path)
    assert typed(dev) == ["O", "K", "SPACE", "G", "O", "SPACE"]
    assert eng.state == READY and eng.last_text == "ok go"
    assert not os.path.exists(path)


def test_press_enter_option(tmp_path):
    eng, dev, _ = make_engine(tmp_path, "hi", press_enter=True, add_space=False)
    eng._finish(eng.recorder.stop())
    assert typed(dev) == ["H", "I", "ENTER"]


def test_empty_transcript_types_nothing(tmp_path):
    eng, dev, _ = make_engine(tmp_path, "")
    eng._finish(eng.recorder.stop())
    assert dev.events == [] and eng.state == READY


def test_talk_ignored_until_ready(tmp_path):
    eng, _, _ = make_engine(tmp_path)
    eng.state = "loading"
    eng.start_talking()
    assert not eng.recorder.recording


def test_no_permission_gives_friendly_error(tmp_path):
    def denied():
        raise PermissionError(13, "Permission denied")

    states = []
    eng = Engine(config.load(tmp_path / "x"), on_state=lambda s, m: states.append((s, m)),
                 typist_factory=denied)
    eng.load()
    assert eng.state == ERROR and "installer" in states[-1][1]


def test_copy_paste_hotkeys(tmp_path):
    eng, dev, _ = make_engine(tmp_path)
    eng.hotkey("copy", True)
    eng.hotkey("copy", False)
    eng.hotkey("paste", True)
    assert typed(dev) == ["LEFTCTRL", "C", "LEFTCTRL", "V"]


# -- SteamVR files -----------------------------------------------------------------
SVR = Path(__file__).parent.parent / "frame_voice" / "steamvr"


def test_steamvr_bindings_only_use_declared_actions():
    from frame_voice.vr import ACTIONS, PRESETS

    manifest = json.loads((SVR / "actions.json").read_text())
    declared = {a["name"].lower() for a in manifest["actions"]}
    assert {f"/actions/{p}/in/{a}" for p in PRESETS for a in ACTIONS} == declared
    for entry in manifest["default_bindings"]:
        b = json.loads((SVR / entry["binding_url"]).read_text())
        assert b["controller_type"] == entry["controller_type"]
        for preset, body in b["bindings"].items():
            outs = [c["output"] for c in body.get("chords", [])]
            outs += [i["output"] for s in body.get("sources", []) for i in s["inputs"].values()]
            assert outs and all(o in declared for o in outs), preset
            assert f"{preset}/in/talk" in outs


def test_frame_controller_is_first_default_binding():
    manifest = json.loads((SVR / "actions.json").read_text())
    assert manifest["default_bindings"][0]["controller_type"] == "frame_controller"
