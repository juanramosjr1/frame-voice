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
    def __init__(self, cfg, on_state, on_transcript):
        self.cfg, self.on_state, self.on_transcript = cfg, on_state, on_transcript
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
    labels = {b.text(): b for b in win.keys}
    assert list(labels) == ["Copy", "Paste", "Cut", "Select all", "Undo", "Delete", "Space", "Enter"]
    labels["Copy"].click()
    labels["Paste"].click()
    assert win.engine.calls == ["copy", "paste"]


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
    win.on_transcript("hello")
    assert win.copy_text.isEnabled() and "hello" in win.last.text()
    win.copy_last()
    assert app.clipboard().text() == "hello"


def test_window_never_takes_focus(win):
    from PySide6.QtCore import Qt

    assert win.windowFlags() & Qt.WindowDoesNotAcceptFocus
    assert all(b.focusPolicy() == Qt.NoFocus for b in win.keys)


def test_controller_preset_switch(win, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    s = ui.Settings(win, win.cfg, win.engine, win.vr)
    s._preset("simple")
    assert win.cfg["controller_preset"] == "simple" and win.vr.preset == "simple"
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
