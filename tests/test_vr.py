"""Controller shortcuts: the SteamVR files, the combo rules, and the SteamVR
thread driven frame by frame against a fake SteamVR."""

import importlib.util
import json
from pathlib import Path

import pytest

from frame_voice import vr
from frame_voice.vr import (BUTTONS, PRESETS, buttons_to_take, combo_actions, legacy_buttons,
                            trigger_partners)

from .fake_openvr import FakeOpenVR

SVR = Path(vr.__file__).parent / "steamvr"
GLOBAL = FakeOpenVR.k_nActionSetOverlayGlobalPriorityMin + 0x100

# Valve, "Steam Frame Input": every input on frame_controller (minus system,
# which apps never get). A/B/X/Y are all on the right controller.
FRAME_PATHS = {f"/user/hand/right/input/{c}" for c in
               ("a", "b", "x", "y", "menu", "bumper", "trigger", "grip", "thumbstick")}
FRAME_PATHS |= {f"/user/hand/left/input/{c}" for c in
                ("dpad_up", "dpad_down", "dpad_left", "dpad_right", "view", "bumper",
                 "trigger", "grip", "thumbstick")}


# -- SteamVR files -----------------------------------------------------------
def load(name):
    return json.loads((SVR / name).read_text())


def all_bindings():
    for entry in load("actions.json")["default_bindings"]:
        yield entry["controller_type"], load(entry["binding_url"])


def test_files_match_generator(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "gen", Path(__file__).parent.parent / "tools" / "gen_bindings.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    monkeypatch.setattr(gen, "OUT", tmp_path)
    gen.main()
    for f in tmp_path.iterdir():
        assert f.read_text() == (SVR / f.name).read_text(), f"{f.name}: run tools/gen_bindings.py"
    assert {f.name for f in tmp_path.iterdir()} == {f.name for f in SVR.glob("*.json")}


def test_one_action_set_per_button():
    manifest = load("actions.json")
    assert [s["name"] for s in manifest["action_sets"]] == [f"/actions/{b}" for b in BUTTONS]
    assert [a["name"] for a in manifest["actions"]] == [f"/actions/{b}/in/press" for b in BUTTONS]
    assert manifest["default_bindings"][0]["controller_type"] == "frame_controller"


def test_every_binding_is_a_plain_button_per_set():
    for ctype, b in all_bindings():
        assert b["controller_type"] == ctype and b["app_key"] == vr.APP_KEY
        assert b["category"] == "steamvr_input"
        assert set(b["bindings"]) == {f"/actions/{x}" for x in BUTTONS}
        for name, body in b["bindings"].items():
            assert "chords" not in body  # combos are worked out in vr.py
            (source,) = body["sources"]
            assert source["mode"] == ("trigger" if name == "/actions/trigger" else "button")
            assert source["inputs"] == {"click": {"output": f"{name}/in/press"}}


def test_frame_controller_paths_exist_on_the_frame():
    (frame,) = [b for ctype, b in all_bindings() if ctype == "frame_controller"]
    paths = {body["sources"][0]["path"] for body in frame["bindings"].values()}
    assert paths <= FRAME_PATHS, paths - FRAME_PATHS
    assert all(p.startswith("/user/hand/right/") for p in paths)


# -- combo rules -------------------------------------------------------------
def test_talk_combos():
    assert combo_actions("ab", True, {"a", "b"}) == {"talk"}
    assert combo_actions("ab", True, {"b"}) == set()
    assert combo_actions("trigger", True, {"trigger", "b"}) == {"talk"}
    assert combo_actions("hold_b", True, {"b"}) == {"talk"}
    assert combo_actions("ab", True, {"a", "b", "trigger"}) == {"talk"}  # talk wins


@pytest.mark.parametrize("preset", PRESETS)
def test_edit_combos_need_the_trigger(preset):
    assert combo_actions(preset, True, {"trigger", "a"}) == {"paste"}
    assert combo_actions(preset, True, {"trigger", "y"}) == {"copy"}
    assert combo_actions(preset, True, {"trigger", "x"}) == {"select_all"}
    assert combo_actions(preset, True, {"a"}) == set()
    assert combo_actions(preset, False, {"trigger", "a"}) == set()


def test_which_buttons_are_taken():
    assert buttons_to_take("ab", True) == {"a", "b", "x", "y"}
    assert buttons_to_take("ab", False) == {"a", "b"}
    assert buttons_to_take("trigger", False) == {"b"}
    assert buttons_to_take("hold_b", False) == {"b"}
    for preset in PRESETS:
        assert "trigger" not in buttons_to_take(preset, True)  # the laser's click
    assert trigger_partners("ab", False) == set()
    assert trigger_partners("trigger", False) == {"b"}
    assert trigger_partners("ab", True) == {"a", "x", "y"}


def test_legacy_bits():
    assert legacy_buttons(1 << 7 | 1 << 1) == {"a", "b"}
    assert legacy_buttons(1 << 33) == {"trigger"}
    assert legacy_buttons(0) == set()


# -- the SteamVR thread, frame by frame --------------------------------------
class FakeReader:
    """Stands in for vrws.ButtonReader (SteamVR's binding page, read-only)."""

    def __init__(self):
        self.connected = False
        self.pressed = set()
        self.started = False

    def start(self):
        self.started = True

    def buttons(self):
        return set(self.pressed) if self.connected else set()

@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(vr, "DIAG_PATH", tmp_path / "steamvr.json")
    monkeypatch.setattr(vr, "write_vrmanifest", lambda: tmp_path / "x.vrmanifest")
    fake = FakeOpenVR()
    calls = []
    svr = vr.SteamVR(lambda action, pressed: calls.append((action, pressed)),
                     reader=FakeReader())
    svr.open(fake)

    def frame(pressed=None, n=1):
        if pressed is not None:
            fake.pressed = set(pressed)
        for _ in range(n):
            assert svr.step()
        return fake.frames[-1]

    svr.fake, svr.calls, svr.frame = fake, calls, frame
    return svr


def test_hold_a_b_talks_and_release_stops(session):
    taken = session.frame()
    assert taken == {b: GLOBAL for b in ("a", "b", "x", "y")}  # not the trigger
    session.frame({"a", "b"})
    assert session.calls == [("talk", True)]
    session.frame({"a"})
    assert session.calls == [("talk", True), ("talk", False)]
    assert session.status == "on"


def test_trigger_is_only_taken_while_a_partner_is_held(session):
    assert "trigger" not in session.frame({"trigger"})  # just the laser click
    assert "trigger" in session.frame({"trigger", "a"}, n=2)
    assert session.calls == [("paste", True)]
    assert "trigger" not in session.frame(set(), n=2)
    assert session.calls == [("paste", True), ("paste", False)]


def test_trigger_layout(session):
    session.set_preset("trigger")
    session.frame({"trigger", "b"}, n=2)
    assert session.calls == [("talk", True)]
    session.frame({"b"})
    assert session.calls[-1] == ("talk", False)


def test_without_edit_shortcuts_x_and_y_stay_free(session):
    session.set_options(edits=False)
    assert set(session.frame({"trigger", "a"}, n=2)) == {"a", "b"}
    assert session.calls == []


def test_paused_during_a_game_unless_menu_open(session):
    session.fake.scene_pid = 4242
    session._next_slow = 0
    taken = session.frame({"a", "b"}, n=2)
    assert set(taken.values()) == {0}  # normal priority: the game keeps its buttons
    assert session.calls == [] and session.status == "paused"
    session.fake.dashboard = True  # Steam menu open over the game
    session._next_slow = 0
    assert set(session.frame(n=2).values()) == {GLOBAL}
    assert session.calls == [("talk", True)]
    session.fake.dashboard = False
    session._next_slow = 0
    session.frame()
    assert session.calls[-1] == ("talk", False)  # never left stuck on


def test_in_games_option_keeps_shortcuts(session):
    session.set_options(in_games=True)
    session.fake.scene_pid = 4242
    session._next_slow = 0
    session.frame({"a", "b"}, n=2)
    assert session.calls == [("talk", True)]


def test_setting_off_is_reported_and_can_be_turned_on(session):
    session.fake.global_setting = False
    session._next_slow = 0
    session.frame({"a", "b"}, n=2)
    assert session.calls == [] and session.status == "setting"
    session.enable_global_input()
    session.frame(n=2)
    assert session.fake.global_setting is True
    assert session.calls == [("talk", True)] and session.status == "on"


def test_unset_setting_counts_as_off(session):
    session.fake.global_setting = None
    session._next_slow = 0
    session.frame()
    assert session.global_input is False and session.status == "setting"


def test_refused_priority_falls_back_to_normal(session):
    session.fake.reject_global = True
    session.fake.global_setting = False
    session._next_slow = 0
    session.frame(n=2)
    assert session.global_rejected and set(session.fake.frames[-1].values()) == {0}
    session.fake.focus = True  # with focus, normal priority still works
    session.frame({"a", "b"})
    assert session.calls == [("talk", True)]


def test_no_bindings_is_reported(session, monkeypatch):
    session.fake.bound = False
    monkeypatch.setattr(vr.SteamVR, "BINDING_GRACE", 0.0)
    session.frame(n=2)
    assert session.status == "nobind" and session.source is None


def test_legacy_buttons_count_too(session):
    session.fake.bound = False
    session.fake.legacy_bits = 1 << 7 | 1 << 1  # A + B
    session._next_slow = 0
    session.frame()
    assert session.calls == [("talk", True)] and session.source == "basic buttons"


def test_quit_and_diagnostics(session):
    session.frame()
    diag = json.loads(vr.DIAG_PATH.read_text())
    assert diag["status"] == "on" and diag["bound"] == ["a", "b", "x", "y"]
    assert diag["controllers"] == ["frame_controller (left)", "frame_controller (right)"]
    session.fake.events.append(FakeOpenVR.VREvent_Quit)
    assert session.step() is False


def test_autolaunch_follows_setting(session):
    session.set_autolaunch(False)
    session.frame()
    assert session.fake.autolaunch[-1] is False


def test_read_only_source_works_with_the_setting_off(session):
    session.fake.global_setting = False
    session.reader.connected = True
    session._next_slow = 0
    session.reader.pressed = {"a", "b"}
    session.frame(n=2)
    assert session.calls == [("talk", True)]
    assert session.status == "partial"  # works, but Turn on is still offered
    assert session.reader.started


def test_read_only_source_means_the_trigger_is_never_taken(session):
    session.reader.connected = True
    session.reader.pressed = {"trigger", "y"}
    taken = session.frame(n=3)
    assert "trigger" not in taken
    assert session.calls == [("copy", True)]


def test_read_only_source_is_ignored_during_games(session):
    session.reader.connected = True
    session.reader.pressed = {"a", "b"}
    session.fake.scene_pid = 99
    session._next_slow = 0
    session.frame(n=2)
    assert session.calls == []


# -- vrserver web socket parsing ----------------------------------------------
def server_frame(text, opcode=0x1, final=True):
    data = text.encode()
    head = bytes([(0x80 if final else 0) | opcode])
    if len(data) < 126:
        head += bytes([len(data)])
    else:
        head += bytes([126]) + len(data).to_bytes(2, "big")
    return head + data


def test_web_socket_frames_and_messages():
    from frame_voice.vrws import VrSocket, apply_message

    class Sock:
        sent = []

        def sendall(self, data):
            self.sent.append(data)

    ws = VrSocket(sock=Sock())
    msg = json.dumps({"type": "update_component_states", "device": "/user/hand/right",
                      "components": {"/input/a/click": True, "/input/b/touch": True,
                                     "/input/trigger/click": False}})
    long_msg = json.dumps({"type": "other", "pad": "x" * 300})
    ws.buf += (server_frame(msg[:10], final=False) + server_frame(msg[10:], opcode=0x0)
               + server_frame(long_msg) + server_frame("partial")[:3])
    out = ws._parse()
    assert out == [msg, long_msg]
    assert bytes(ws.buf) == server_frame("partial")[:3]  # waits for the rest
    state = {}
    for text in out:
        apply_message(text, ["/user/hand/right"], state)
    assert state == {"a": True, "trigger": False}
    apply_message(msg.replace("right", "left"), ["/user/hand/right"], state)
    apply_message("not json update_component_states", ["/user/hand/right"], state)
    assert state == {"a": True, "trigger": False}


def test_client_frames_are_masked():
    from frame_voice.vrws import VrSocket

    class Sock:
        sent = []

        def sendall(self, data):
            self.sent.append(data)

    sock = Sock()
    ws = VrSocket(sock=sock)
    ws.open("box")
    frame = sock.sent[-1]
    assert frame[0] == 0x81 and frame[1] & 0x80  # final text frame, masked
    n = frame[1] & 0x7F
    mask, payload = frame[2:6], frame[6:6 + n]
    assert bytes(b ^ mask[i % 4] for i, b in enumerate(payload)) == b"mailbox_open box"


def test_button_reader_against_a_local_vrserver(monkeypatch):
    """The whole read-only path: device list over HTTP, then the web socket."""
    import socketserver
    import threading
    import time

    from frame_voice import vrws

    seen = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            req = b""
            while b"\r\n\r\n" not in req:
                req += self.request.recv(4096)
            if req.startswith(b"GET /input/getstate.json"):
                body = json.dumps({"devices": [
                    {"controller_type": "frame_controller", "side": "right", "root_path": "/user/hand/right"},
                    {"controller_type": "frame_controller", "side": "left", "root_path": "/user/hand/left"},
                ]}).encode()
                self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                     b"Content-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))
                return
            seen.append(req)
            self.request.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                                 b"Connection: Upgrade\r\n\r\n")
            time.sleep(0.2)
            msg = json.dumps({"type": "update_component_states", "device": "/user/hand/right",
                              "components": {"/input/a/click": True, "/input/b/click": True}})
            self.request.sendall(server_frame(msg))
            time.sleep(2)

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(vrws, "PORT", port)
    monkeypatch.setattr(vrws, "ORIGIN", origin)
    monkeypatch.setattr(vrws, "HEADERS", {"Referer": f"{origin}/dashboard/controllerbinding.html"})
    reader = vrws.ButtonReader()
    reader.start()
    try:
        deadline = time.monotonic() + 5
        while reader.buttons() != {"a", "b"} and time.monotonic() < deadline:
            time.sleep(0.05)
        assert reader.buttons() == {"a", "b"}
        assert b"Referer: " + origin.encode() in seen[0]
    finally:
        reader.stop()
        server.shutdown()
