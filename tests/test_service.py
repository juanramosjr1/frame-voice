"""Starting with SteamVR, and the background copy stepping aside for the window."""

import fcntl
import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from frame_voice import autostart, config, service
from frame_voice import log as log_mod


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(log_mod, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    yield tmp_path / "state"
    for role in list(service._held):
        service.release_lock(role)


def wait_for(cond, seconds=3):
    deadline = time.monotonic() + seconds
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


# -- one copy per role --------------------------------------------------------------
def test_lock_keeps_a_second_copy_out(state):
    assert service.hold_lock("background")
    assert service.hold_lock("background")  # this copy already has it
    # another process trying the same lock
    fd = os.open(state / "background.lock", os.O_RDWR)
    with pytest.raises(OSError):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.close(fd)
    assert service.hold_lock("window")  # the roles don't block each other


def test_lock_held_by_another_copy(state):
    state.mkdir(parents=True)
    fd = os.open(state / "window.lock", os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert not service.hold_lock("window")
        assert not service.wait_for_lock("window", 0.2)
    finally:
        os.close(fd)
    assert service.wait_for_lock("window", 1)


# -- the connection between the two copies ------------------------------------------
def make_server():
    events = []
    server = service.Server(lambda open_now: events.append(open_now),
                            lambda: events.append("quit"), lambda: {"engine": "ready"})
    server.start()
    return server, events


def test_other_users_are_refused(monkeypatch):
    class Conn:
        def getsockopt(self, *args):
            import struct
            return struct.pack("3i", 1, os.getuid() + 1, 0)

    assert not service.same_user(Conn())


def test_each_state_folder_has_its_own_socket(state, monkeypatch, tmp_path):
    name = service.socket_name()
    assert name.startswith("\0") and str(os.getuid()) in name
    monkeypatch.setattr(log_mod, "STATE_DIR", tmp_path / "other")
    assert service.socket_name() != name


def test_no_background_copy_means_no_link(state):
    assert service.Link.open() is None
    assert service.status() is None


def test_window_link_pauses_and_resumes(state):
    server, events = make_server()
    try:
        server.last_text = "said earlier"
        link = service.Link.open()
        assert link.last_text == "said earlier"
        wait_for(lambda: events == [True])
        assert server.has_window()
        link.send_last("new words")
        wait_for(lambda: server.last_text == "new words")
        lost = []
        link.watch(lambda: lost.append(True))
        link.close()
        wait_for(lambda: events == [True, False])
        assert not server.has_window()
    finally:
        server.close()
    wait_for(lambda: lost == [True])


def test_quit_from_the_window_stops_the_background_copy(state):
    server, events = make_server()
    try:
        link = service.Link.open()
        link.quit()
        link.close()
        wait_for(lambda: events == [True, "quit"])
    finally:
        server.close()


def test_status_doesnt_count_as_a_window(state):
    server, events = make_server()
    try:
        info = service.status()
        assert info["pid"] == os.getpid() and info["windows"] == 0 and info["engine"] == "ready"
        time.sleep(0.05)
        assert events == []
    finally:
        server.close()


def test_link_notices_the_background_copy_ending(state):
    server, events = make_server()
    link = service.Link.open()
    lost = []
    link.watch(lambda: lost.append(True))
    wait_for(lambda: events == [True])
    server.close()  # SteamVR stopped, and the background copy with it
    wait_for(lambda: lost == [True] and not link.alive)


# -- opening the app while its window copy runs ------------------------------------
def test_opening_again_in_the_same_session_shows_the_window(state):
    events = []
    server = service.WindowServer(lambda: events.append("show"), lambda: events.append("move"),
                                  here=":0||/run/user/1000")
    server.start()
    try:
        assert service.ask_window(":0||/run/user/1000") == "shown"
        wait_for(lambda: events == ["show"])
        # opened from the Frame's desktop: the window moves there
        assert service.ask_window(":1|wayland-0|/tmp/desktop-runtime") == "bye"
        wait_for(lambda: events == ["show", "move"])
    finally:
        server.close()
    assert service.ask_window() == ""  # nothing running


def test_place_tells_the_sessions_apart(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "gamescope-0")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    steam = service.place()
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp/desktop-runtime")
    assert service.place() != steam


# -- the background copy ------------------------------------------------------------
class FakeEngine:
    def __init__(self, cfg, on_state, on_transcript):
        self.cfg, self.on_state, self.on_transcript = cfg, on_state, on_transcript
        self.state = "loading"
        self.paused = False
        self.transcriber = None
        self.calls = []

    def load(self):
        self.transcriber = object()
        self.state = "ready"

    def start_hotkeys(self):
        self.calls.append("hotkeys")

    def cancel_talking(self):
        self.calls.append("cancel")

    def hotkey(self, action, pressed):
        self.calls.append((action, pressed))

    def reload_model(self, model):
        self.cfg["model"] = model
        self.calls.append(("model", model))


class FakeVR:
    def __init__(self, on_action, preset, edits, in_games, autolaunch):
        self.preset, self.edits, self.in_games = preset, edits, in_games
        self.autolaunch = autolaunch
        self._hold = False
        self.started = False
        self.status, self.summary = "on", "Hold A and B together"
        self.states = []

    @property
    def hold(self):
        return self._hold

    @hold.setter
    def hold(self, on):  # the real one gets there from its own thread
        self._hold = on
        self.status = "held" if on else "on"

    def start(self):
        self.started = True

    def set_preset(self, preset):
        self.preset = preset

    def set_options(self, edits=None, in_games=None):
        self.edits, self.in_games = edits, in_games

    def show_state(self, state, message=""):
        self.states.append(state)


def make_background(grace=0.0):
    copied, quits = [], []
    bg = service.Background(config.load(), FakeEngine, vr_cls=FakeVR, copy=copied.append,
                            on_quit=lambda: quits.append(True), grace=grace)
    bg.copied, bg.quits = copied, quits
    return bg


def test_background_copy_takes_the_shortcuts_once_ready():
    bg = make_background(grace=0.1)
    assert bg.vr.hold and bg.engine.paused  # nothing until the model is loaded
    bg.start()
    try:
        assert bg.vr.started
        wait_for(lambda: not bg.vr.hold)
        assert not bg.engine.paused and bg.engine.state == "ready"
    finally:
        bg.server.close()


def test_background_copy_steps_aside_for_the_window_and_picks_up_new_settings():
    bg = make_background()
    bg.start()
    try:
        wait_for(lambda: not bg.vr.hold)
        bg.engine.on_transcript("from the background")
        assert bg.copied == ["from the background"]
        link = service.Link.open()
        assert link.last_text == "from the background"
        wait_for(lambda: bg.vr.hold and bg.engine.paused)
        assert "cancel" in bg.engine.calls  # a dictation in progress is dropped, not typed
        # the window changes the settings, then closes
        cfg = config.load()
        cfg.update(controller_preset="hold_b", edit_shortcuts=False, model="small.en",
                   autostart=False)
        config.save(cfg)
        link.close()
        wait_for(lambda: not bg.vr.hold)
        assert not bg.engine.paused
        assert bg.vr.preset == "hold_b" and bg.vr.edits is False
        assert ("model", "small.en") in bg.engine.calls
        assert bg.vr.autolaunch is False  # turning off "Start automatically" sticks
    finally:
        bg.server.close()


def test_window_opening_during_the_grace_time_keeps_the_background_copy_off():
    bg = make_background(grace=0.2)
    bg.start()
    try:
        link = service.Link.open()
        time.sleep(0.4)
        assert bg.vr.hold and bg.ready
        link.close()
        wait_for(lambda: not bg.vr.hold)
    finally:
        bg.server.close()


def test_window_that_came_and_went_skips_the_grace_time():
    bg = make_background(grace=30)
    bg.start()
    try:
        link = service.Link.open()  # the closing window that started this copy
        link.close()
        wait_for(lambda: not bg.vr.hold)
    finally:
        bg.server.close()


def test_answer_waits_until_the_background_copy_is_off_steamvr():
    bg = make_background()
    bg.start()
    try:
        wait_for(lambda: not bg.vr.hold)
        order = []
        real = bg._settle

        def settle():
            real()
            order.append(bg.vr.status)

        bg.server.settle = settle
        link = service.Link.open()
        assert order == ["held"]  # answered only after letting go of SteamVR
        link.close()
    finally:
        bg.server.close()


def test_background_status_and_quit():
    bg = make_background()
    bg.start()
    try:
        wait_for(lambda: not bg.vr.hold)
        info = service.status()
        assert info["engine"] == "ready" and info["shortcuts"] == "on"
        link = service.Link.open()
        link.quit()
        link.close()
        wait_for(lambda: bg.quits == [True])
    finally:
        bg.server.close()


def test_pick_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(service.os.path, "exists", lambda p: False)
    assert service.pick_display() is None
    monkeypatch.setattr(service.os.path, "exists", lambda p: p == "/tmp/.X11-unix/X0")
    monkeypatch.setattr(service, "x_display_works", lambda name: name == ":0")
    assert service.pick_display() == ":0"  # started without a display: the Steam session's
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setattr(service, "x_display_works", lambda name: True)
    assert service.pick_display() == ":1"  # its own comes first


def test_display_that_doesnt_answer():
    assert service.x_display_works(":4321", timeout=2) is False


# -- starting with SteamVR (a user service) ------------------------------------------
@pytest.fixture
def fake_systemctl(tmp_path, monkeypatch):
    calls = []
    units = {"steamvr.service"}
    enabled = set()

    def run(*args):
        calls.append(args)
        if args[0] == "cat":
            return SimpleNamespace(returncode=0 if args[1] in units else 1, stdout="", stderr="")
        if args[0] == "is-enabled":
            return SimpleNamespace(returncode=0, stdout="enabled\n" if args[1] in enabled
                                   else "disabled\n", stderr="")
        if args[0] == "enable":
            enabled.add(args[1])
        if args[0] == "disable":
            enabled.discard(args[1])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(autostart, "systemctl", run)
    monkeypatch.setattr(autostart, "UNIT_DIR", tmp_path / "units")
    return SimpleNamespace(calls=calls, units=units, enabled=enabled)


def test_unit_starts_and_stops_with_steamvr():
    text = autostart.unit_text('"/venv/bin/frame-voice"')
    for line in ("After=steamvr.service", "PartOf=steamvr.service", "Requisite=steamvr.service",
                 "WantedBy=steamvr.service", 'ExecStart="/venv/bin/frame-voice" --background',
                 "Restart=on-failure"):
        assert line in text.splitlines()


def test_turning_start_with_steamvr_on_and_off(fake_systemctl):
    assert autostart.set_enabled(True, command='"/venv/bin/frame-voice"')
    unit = autostart.UNIT_DIR / autostart.UNIT
    assert "WantedBy=steamvr.service" in unit.read_text()
    assert ("daemon-reload",) in fake_systemctl.calls
    assert autostart.enabled()
    fake_systemctl.calls.clear()
    assert autostart.set_enabled(True, command='"/venv/bin/frame-voice"')
    assert ("daemon-reload",) not in fake_systemctl.calls  # unchanged file: no reload
    assert autostart.start_now()
    assert ("start", autostart.UNIT) in fake_systemctl.calls
    assert autostart.set_enabled(False)
    assert not autostart.enabled() and not autostart.start_now()


def test_no_steamvr_service_means_no_unit(fake_systemctl):
    fake_systemctl.units.clear()  # not SteamOS on the Frame
    assert not autostart.available()
    assert not autostart.set_enabled(True)
    assert not (autostart.UNIT_DIR / autostart.UNIT).exists()


def test_systemctl_reaches_the_real_user_manager(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(autostart.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(autostart.subprocess, "run", fake_run)
    monkeypatch.setattr(autostart.Path, "exists", lambda self: str(self).endswith("/bus"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp/nested-desktop")
    autostart.systemctl("is-enabled", autostart.UNIT)
    runtime = f"/run/user/{os.getuid()}"
    assert seen["cmd"] == ["/usr/bin/systemctl", "--user", "is-enabled", autostart.UNIT]
    assert seen["env"]["XDG_RUNTIME_DIR"] == runtime
    assert seen["env"]["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={runtime}/bus"


def test_systemctl_missing(monkeypatch):
    monkeypatch.setattr(autostart.shutil, "which", lambda name: None)
    assert autostart.systemctl("cat", "steamvr.service") is None
    assert not autostart.available() and not autostart.enabled()


def test_systemd_user_unit_is_valid(fake_systemctl):
    verify = subprocess.run(["which", "systemd-analyze"], capture_output=True, text=True)
    if verify.returncode != 0:
        pytest.skip("systemd-analyze not installed")
    autostart.set_enabled(True, command='"/bin/true"')
    unit = autostart.UNIT_DIR / autostart.UNIT
    # (system mode: --user needs a login session, which test machines lack)
    out = subprocess.run(["systemd-analyze", "verify", str(unit)], capture_output=True, text=True)
    # steamvr.service doesn't exist here; anything else is a mistake in our unit
    problems = [line for line in (out.stdout + out.stderr).splitlines()
                if line.strip() and "steamvr.service" not in line]
    assert problems == []
