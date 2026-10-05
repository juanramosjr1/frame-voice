import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from frame_voice import config, ui  # noqa: E402
from frame_voice.app import LISTENING, READY  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeEngine:
    def __init__(self, cfg, on_state, on_transcript, before_input):
        self.cfg, self.on_state, self.on_transcript = cfg, on_state, on_transcript
        self.before_input = before_input
        self.state = READY
        self.last_text = ""
        self.calls = []

    def load(self):
        self.on_state(READY, "Ready")

    def shortcut(self, name):
        self.calls.append(name)

    def toggle_talking(self):
        self.calls.append("toggle")

    def hotkey(self, action, pressed):
        self.calls.append((action, pressed))

    def retype_last(self):
        self.calls.append("retype")

    def reload_model(self, model):
        self.calls.append(("model", model))


@pytest.fixture
def win(app, tmp_path):
    w = ui.MainWindow(config.load(tmp_path / "c.json"), FakeEngine)
    yield w
    w.vr.stop()
    w.deleteLater()


def test_edit_keys_send_shortcuts(win):
    import time

    labels = {b.text(): b for b in win.keys}
    assert list(labels) == ["Copy", "Paste", "Cut", "Select all", "Undo", "Delete", "Space", "Enter"]
    labels["Copy"].click()
    labels["Paste"].click()
    deadline = time.monotonic() + 2
    while len(win.engine.calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sorted(win.engine.calls) == ["copy", "paste"]


def test_mic_toggles_and_states_update_text(win):
    win.mic.click()
    assert win.engine.calls == ["toggle"]
    win.on_state(LISTENING, "Listening...")
    assert win.status.text() == "Listening..."
    win.on_state(READY, "Ready")
    assert win.status.text() == "Tap to talk"


def test_transcript_enables_buttons(win, app):
    assert not win.copy_text.isEnabled()
    win.engine.last_text = "hello"
    app.clipboard().setText("")
    win.on_transcript("hello")
    assert win.copy_text.isEnabled() and "hello" in win.last.text()
    assert app.clipboard().text() == "hello"  # every result lands on the clipboard
    app.clipboard().setText("")
    win.copy_last()
    assert app.clipboard().text() == "hello"


def test_window_never_takes_focus(win):
    from PySide6.QtCore import Qt

    assert win.windowFlags() & Qt.WindowDoesNotAcceptFocus
    # No widget may take keyboard focus: if one did, dictated text typed into
    # our window could "press" it (a space used to open Settings).
    takers = [w.objectName() or type(w).__name__ for w in win.findChildren(QtWidgets.QWidget)
              if w.focusPolicy() != Qt.NoFocus]
    assert takers == []


def test_typed_keys_never_trigger_our_buttons(win, monkeypatch):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    opened = []
    monkeypatch.setattr(ui.Settings, "exec", lambda self: opened.append(True))
    win.show()
    for w in [win] + win.findChildren(QtWidgets.QWidget):
        for kind in (QEvent.KeyPress, QEvent.KeyRelease):
            QtWidgets.QApplication.sendEvent(w, QKeyEvent(kind, Qt.Key_Space, Qt.NoModifier, " "))
    assert opened == [] and win.engine.calls == []


def test_settings_takes_no_focus_and_ignores_typed_keys(win, monkeypatch):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    s = ui.Settings(win, win.cfg, win.engine, win.vr)
    takers = [type(w).__name__ for w in s.findChildren(QtWidgets.QWidget)
              if w.focusPolicy() != Qt.NoFocus]
    assert takers == []
    quits = []
    monkeypatch.setattr(QtWidgets.QApplication, "quit", lambda: quits.append(True))
    s.show()
    for w in [s] + s.findChildren(QtWidgets.QWidget):
        for key, text in ((Qt.Key_Space, " "), (Qt.Key_Return, "\r"), (Qt.Key_Escape, "")):
            QtWidgets.QApplication.sendEvent(w, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, text))
    assert quits == [] and s.isVisible()
    s.close()


def test_controller_presses_only_light_up_while_settings_is_open(win):
    win._testing = True
    win.on_controller("talk", True)
    win.on_controller("talk", False)  # releases always pass
    assert win.engine.calls == [("talk", False)]
    win._testing = False
    win.on_controller("paste", True)
    assert win.engine.calls[-1] == ("paste", True)


def test_turn_on_button_shows_when_setting_is_off(win, app):
    calls = []
    win.vr.enable_global_input = lambda: calls.append(True)
    win.vr.status = "setting"
    win.on_vr_status()
    assert not win.turn_on.isHidden()
    assert "SteamVR setting" in win.controller.text()
    win.turn_on.click()
    assert calls == [True]
    win.vr.status, win.vr.summary = "on", "ok"
    win.on_vr_status()
    assert win.turn_on.isHidden()
    assert win.controller.text().startswith("Hold A and B together")
    win.on_state(READY, "Ready")
    assert win.hint.text() == "Click a text box, then hold A + B and talk."


def test_settings_test_panel_lights_buttons(win):
    s = ui.Settings(win, win.cfg, win.engine, win.vr)
    win.vr.snapshot = lambda: {"summary": "x", "status": "setting", "buttons": {"a"},
                               "held": set(), "bound": {"a": True, "b": True}}
    s._refresh_test()
    assert not s.turn_on.isHidden()
    assert "#2ee6b8" in s.pills["btn:a"].styleSheet()       # lit
    assert "dashed" in s.pills["btn:trigger"].styleSheet()  # not bound
    s.close()


def test_focus_guard_passes_when_window_inactive(win):
    win._active = False
    assert win.give_focus_back() is True


def test_controller_preset_switch(win, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    s = ui.Settings(win, win.cfg, win.engine, win.vr)
    assert [b.text() for b in s.presets.buttons()] == ["Hold A + B", "Trigger + B", "Hold B"]
    assert s.presets.checkedButton().text() == "Hold A + B"
    s._preset("hold_b")
    assert win.cfg["controller_preset"] == "hold_b" and win.vr.preset == "hold_b"
    assert (tmp_path / "config.json").exists()


def test_intro_runs_quickly_and_finishes(app):
    from PySide6.QtCore import QElapsedTimer

    intro = ui.Intro()
    done = []
    intro.finished.connect(lambda: done.append(True))
    timer = QElapsedTimer()
    timer.start()
    intro.start()
    while not done and timer.elapsed() < 4000:
        app.processEvents()
    assert done, "intro never finished"
    assert timer.elapsed() < 2500


def test_badge_pixels(app):
    from frame_voice.vr import render_pill

    buf = render_pill("Listening...", "#ff4d5e", 512, 128)
    assert len(buf) == 512 * 128 * 4
