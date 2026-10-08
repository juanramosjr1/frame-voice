import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from frame_voice import autostart, config, ui  # noqa: E402
from frame_voice import log as log_mod  # noqa: E402
from frame_voice.app import LISTENING, READY, WORKING  # noqa: E402


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

    def reload_model(self, model):
        self.calls.append(("model", model))

    def cancel_talking(self):
        self.calls.append("cancel")
        if self.state == LISTENING:
            self.state = READY


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(log_mod, "STATE_DIR", tmp_path / "state")


@pytest.fixture
def win(app, tmp_path):
    w = ui.MainWindow(config.load(tmp_path / "c.json"), FakeEngine)
    w.vr.start = lambda: None  # no real SteamVR connection in these tests
    yield w
    w.vr.stop()
    w.deleteLater()


@pytest.fixture
def quits(monkeypatch):
    calls = []
    monkeypatch.setattr(QtWidgets.QApplication, "exit", lambda code=0: calls.append(True))
    # quit() closes the window first, which means "keep running in the background"
    monkeypatch.setattr(QtWidgets.QApplication, "quit", lambda: pytest.fail("use exit()"))
    return calls


class FakeLink:
    def __init__(self, last_text=""):
        self.last_text, self.alive = last_text, True
        self.sent, self.closed, self.on_lost = [], False, None

    def watch(self, on_lost):
        self.on_lost = on_lost

    def send_last(self, text):
        self.sent.append(text)

    def quit(self):
        self.sent.append("quit")

    def close(self):
        self.closed, self.alive = True, False


def test_window_is_just_the_mic_and_copy(win):
    texts = sorted(b.text() for b in win.findChildren(QtWidgets.QAbstractButton) if b.text())
    assert texts == ["Copy", "Turn on"]  # (Turn on only shows when SteamVR needs a setting)
    assert win.turn_on.isHidden()


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
    monkeypatch.setattr(QtWidgets.QApplication, "exit", lambda code=0: quits.append(True))
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


def test_my_words_in_settings(win, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    opened = []
    monkeypatch.setattr(ui.QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toLocalFile()) or True)
    s = ui.Settings(win, win.cfg, win.engine, win.vr)
    assert "None added yet" in s.words_line.text()
    assert s.words_hint.isHidden()
    edit = [b for b in s.findChildren(QtWidgets.QPushButton) if b.text() == "Edit my words"]
    edit[0].click()
    path = tmp_path / "words.txt"
    assert opened == [str(path)] and path.read_text() == config.WORDS_HEADER
    assert not s.words_hint.isHidden() and "save" in s.words_hint.text()
    path.write_text(config.WORDS_HEADER + "fuelCell\nKayleigh\n")
    s._refresh_words()  # the timer does this every second
    assert s.words_line.text() == "fuelCell, Kayleigh"
    # no text editor to open it with: say where the list is
    monkeypatch.setattr(ui.QDesktopServices, "openUrl", lambda url: False)
    edit[0].click()
    assert "words.txt" in s.words_hint.text()
    s.close()


def test_words_summary():
    assert "None added yet" in ui.words_summary([])
    assert ui.words_summary(["fuelCell", "Kayleigh"]) == "fuelCell, Kayleigh"
    many = ui.words_summary([f"Name{i}" for i in range(30)])
    assert many.endswith(" more") and len(many) < 90


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


# -- sharing the work with the background copy ----------------------------------------
def test_window_shows_the_background_copys_last_dictation(win):
    link = FakeLink("said in the background")
    win.on_linked(link)
    assert win.link is link and link.on_lost is not None
    assert "said in the background" in win.last.text() and win.copy_text.isEnabled()
    assert win.engine.last_text == "said in the background"  # so Copy copies it
    win.on_transcript("said in the window")
    assert link.sent == ["said in the window"]  # the background copy remembers it too


def test_shortcuts_start_after_looking_for_the_background_copy(win):
    started = []
    win.vr.start = lambda: started.append(True)
    win.on_linked(None)
    win.on_linked(None)
    assert started == [True] and win.link is None


def test_closing_the_window_hands_back_to_the_background_copy(win, quits):
    win.present(fade=False)
    win.on_linked(FakeLink())
    win.close()
    assert win.isHidden() and quits == [True]


def test_closing_with_no_background_copy_starts_it(win, quits, app, monkeypatch):
    import time

    started = []
    monkeypatch.setattr(autostart, "start_now", lambda: started.append(True) or True)
    win.present(fade=False)
    win.close()
    deadline = time.monotonic() + 2
    while not quits and time.monotonic() < deadline:
        app.processEvents()
    assert started == [True] and quits == [True]


def test_closing_without_start_with_steamvr_keeps_running(win, quits, monkeypatch):
    monkeypatch.setattr(autostart, "start_now", lambda: pytest.fail("shouldn't start it"))
    win.cfg["autostart"] = False
    win.present(fade=False)
    win.close()
    assert win.isHidden() and quits == []


def test_hidden_window_leaves_the_shortcuts_to_a_new_background_copy(win, quits):
    win.present(fade=False)
    win.hide()  # closed earlier, still running for the shortcuts
    link = FakeLink()
    win.on_linked(link)
    assert link.closed and win.link is None and quits == [True]


def test_quit_app_stops_the_background_copy_too(win, quits):
    link = FakeLink()
    win.on_linked(link)
    win.present(fade=False)
    win.quit_app()
    assert link.sent == ["quit"] and quits == [True]


def test_quit_app_never_starts_the_background_copy(win, quits, monkeypatch):
    monkeypatch.setattr(autostart, "start_now", lambda: pytest.fail("Quit app restarted it"))
    win.present(fade=False)
    s = ui.Settings(win, win.cfg, win.engine, win.vr)
    quit_btn = [b for b in s.findChildren(QtWidgets.QPushButton) if b.text() == "Quit app"][0]
    quit_btn.click()
    assert quits == [True]
    s.close()


def test_quit_lets_typing_finish_but_drops_a_recording(win, quits, app):
    import time

    win.engine.state = WORKING
    win.quit_when_idle()
    assert quits == []
    win.engine.state = READY
    deadline = time.monotonic() + 2
    while not quits and time.monotonic() < deadline:
        app.processEvents()
    assert quits == [True]
    win.engine.state = LISTENING  # the mic is on: never wait for it
    win.quit_when_idle()
    assert "cancel" in win.engine.calls and quits == [True, True]


def test_window_reopened_before_the_hand_back_finished_stays(win, quits):
    win.present(fade=False)
    win._after_hand_back()  # the window is showing again
    assert quits == []
    win.hide()
    win._after_hand_back()
    assert quits == [True]


def test_retrying_after_an_error_only_reloads(win, monkeypatch):
    loads = []
    monkeypatch.setattr(win, "load_engine", lambda: loads.append(True))
    monkeypatch.setattr(win, "find_background", lambda: pytest.fail("not again"))
    from frame_voice.app import ERROR
    win.engine.state = ERROR
    win.on_mic()
    assert loads == [True]


def test_show_requests_reach_the_window(win, app):
    import time

    shown = []
    win.bring_back = lambda: shown.append(True)
    win.bridge.show.disconnect()
    win.bridge.show.connect(win.bring_back)
    win.bridge.show.emit()  # from the request thread in real use
    deadline = time.monotonic() + 1
    while not shown and time.monotonic() < deadline:
        app.processEvents()
    assert shown == [True]


def test_intro_has_no_off_switch(win, tmp_path):
    s = ui.Settings(win, win.cfg, win.engine, win.vr)
    labels = [b.text() for b in s.findChildren(QtWidgets.QCheckBox)]
    assert not [t for t in labels if "intro" in t.lower()]  # it always plays
    assert "show_intro" not in config.load(tmp_path / "none.json")
    s.close()


def test_intro_plays_on_the_first_open_after_the_headset_starts(tmp_path):
    boot = tmp_path / "boot_id"
    boot.write_text("boot-1\n")
    assert ui.intro_due(boot)       # first open
    assert not ui.intro_due(boot)   # opened again
    assert not ui.intro_due(boot)
    boot.write_text("boot-2\n")     # the headset restarted
    assert ui.intro_due(boot)
    assert not ui.intro_due(boot)
    assert ui.intro_due(tmp_path / "missing")  # can't tell: play it
