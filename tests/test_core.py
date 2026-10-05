import json
import os
import re
import struct
import time
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
    assert cfg["model"] == "base.en" and cfg["controller_preset"] == "ab"
    assert cfg["after_talking"] == "type"
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
    # the old "grip" layout no longer exists
    path.write_text(json.dumps({"controller_preset": "grip"}))
    assert config.load(path)["controller_preset"] == "ab"
    defaults = config.load(tmp_path / "missing.json")
    assert defaults["edit_shortcuts"] is True and defaults["in_games"] is False
    assert defaults["autostart"] is True


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


def make_engine(tmp_path, text="hello there", focus_ok=True, **cfg_over):
    cfg = config.load(tmp_path / "none.json")
    cfg.update(cfg_over)
    dev = FakeDevice()
    states = []
    transcripts = []

    class Tx:
        def __init__(self, model):
            self.model = model

        def transcribe(self, path):
            assert os.path.exists(path)
            return text

    eng = Engine(cfg, on_state=lambda s, m: states.append((s, m)),
                 on_transcript=transcripts.append,
                 typist_factory=lambda: Typist(dev, delay=0),
                 recorder=FakeRecorder(tmp_path), transcriber_factory=Tx,
                 before_input=lambda: focus_ok)
    eng.transcripts = transcripts
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


def wait_for(cond, timeout=2):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)
    return cond()


def test_copy_paste_hotkeys(tmp_path):
    eng, dev, _ = make_engine(tmp_path)
    eng.hotkey("copy", True)  # runs off the caller's (controller) thread
    eng.hotkey("copy", False)
    assert wait_for(lambda: len(typed(dev)) == 2)
    eng.hotkey("paste", True)
    assert wait_for(lambda: len(typed(dev)) == 4)
    assert typed(dev) == ["LEFTCTRL", "C", "LEFTCTRL", "V"]


def test_focus_guard_crash_does_not_stick_the_app(tmp_path):
    eng, dev, states = make_engine(tmp_path, "hi", add_space=False)

    def broken():
        raise RuntimeError("no display")

    eng.before_input = broken
    eng.state = "working"
    eng._finish(eng.recorder.stop())
    assert eng.state == READY and typed(dev) == ["H", "I"]


def test_load_wav_resamples_and_mixes_to_mono(tmp_path):
    import wave

    import numpy as np

    from frame_voice.voice import load_wav

    path = tmp_path / "a.wav"
    t = np.arange(22050) / 22050
    tone = (np.sin(2 * np.pi * 440 * t) * 16000).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(np.repeat(tone, 2).tobytes())
    audio = load_wav(str(path))
    assert audio.dtype == np.float32
    assert abs(len(audio) - 16000) <= 1
    assert 0.45 < float(np.abs(audio).max()) < 0.5


def test_release_keeps_recording_briefly_then_types(tmp_path):
    eng, dev, states = make_engine(tmp_path, "hi", add_space=False)
    eng.hotkey("talk", True)
    t0 = time.monotonic()
    eng.hotkey("talk", False)
    assert eng.state == "working"
    while eng.state != READY and time.monotonic() - t0 < 3:
        time.sleep(0.01)
    assert time.monotonic() - t0 >= Engine.RELEASE_TAIL
    assert typed(dev) == ["H", "I"] and eng.transcripts == ["hi"]


def test_clipboard_only_mode_types_nothing(tmp_path):
    eng, dev, states = make_engine(tmp_path, "hello", after_talking="clipboard")
    eng._finish(eng.recorder.stop())
    assert dev.events == [] and eng.transcripts == ["hello"]
    assert states[-1] == (READY, "Copied. Paste it where you want it.")


def test_when_our_window_may_have_focus_it_still_types_and_says_paste(tmp_path):
    # Our window swallows typed keys, so typing is harmless if it does have
    # focus, and lands in the right place if the focus guess was stale.
    eng, dev, states = make_engine(tmp_path, "hi", focus_ok=False, add_space=False)
    eng._finish(eng.recorder.stop())
    assert typed(dev) == ["H", "I"]
    assert eng.transcripts == ["hi"]  # on the clipboard too
    assert states[-1] == (READY, "Copied. If it didn't appear, click the text box and paste.")
    eng.shortcut("paste")
    assert typed(dev)[-2:] == ["LEFTCTRL", "V"]


# -- KWin rule: our window never takes keyboard focus on KDE ---------------------
class FakeKConfig:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, group, key):
        return self.data.get(group, {}).get(key, "")

    def put(self, group, key, value):
        self.data.setdefault(group, {})[key] = value

    def delete(self, group, key):
        self.data.get(group, {}).pop(key, None)


def test_kwin_rule_added_once_and_keeps_other_rules(monkeypatch):
    from frame_voice import focus

    monkeypatch.setattr(focus, "_dbus", lambda *a: "")
    conf = FakeKConfig({"General": {"rules": "abc-uuid", "count": "1"}, "abc-uuid": {"x": "1"}})
    assert focus.install_kwin_rule(conf)
    assert conf.data["General"] == {"rules": "abc-uuid,frame-voice", "count": "2"}
    rule = conf.data["frame-voice"]
    assert rule["acceptfocus"] == "false" and rule["acceptfocusrule"] == "2"
    assert rule["wmclass"] == "frame-voice" and rule["types"] == "1"
    assert focus.install_kwin_rule(conf)  # second time: no duplicate
    assert conf.data["General"]["rules"] == "abc-uuid,frame-voice"
    assert focus.remove_kwin_rule(conf)
    assert conf.data["General"] == {"rules": "abc-uuid", "count": "1"}
    assert conf.data["frame-voice"] == {} and conf.data["abc-uuid"] == {"x": "1"}


def test_kwin_rule_with_old_numbered_rules(monkeypatch):
    from frame_voice import focus

    monkeypatch.setattr(focus, "_dbus", lambda *a: "")
    conf = FakeKConfig({"General": {"count": "2"}})
    focus.install_kwin_rule(conf)
    assert conf.data["General"] == {"rules": "1,2,frame-voice", "count": "3"}


def test_kwin_rule_skipped_without_kde_tools(monkeypatch):
    from frame_voice import focus

    conf = focus.KConfig()
    conf.tools = None
    assert focus.install_kwin_rule(conf) is False


def test_failed_kreadconfig_never_wipes_rules(monkeypatch):
    from frame_voice import focus

    conf = focus.KConfig()
    conf.tools = ("/bin/false", "/bin/false")
    assert focus.install_kwin_rule(conf) is False  # read failed: nothing written
