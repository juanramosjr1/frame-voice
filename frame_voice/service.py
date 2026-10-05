"""The background copy, and how it shares the work with the window.

SteamVR starts the app in the background (see autostart.py), where it runs the
controller shortcuts without a window. Opening the app starts a second copy,
with the window. That copy does the work while it runs and the background copy
steps aside, so nothing is typed twice. The window's copy keeps a connection to
the background copy open, and the background copy takes the work back the
moment that connection ends, however the window's copy ended: closed, quit,
crashed, or gone with its desktop session.

The connection also carries the last dictation, so the window can always copy
it again.
"""

import ctypes
import fcntl
import json
import os
import signal
import socket
import struct
import sys
import threading
import time
import zlib

from . import config as config_mod
from .log import log

# How long a starting background copy leaves the controllers to a window that
# may be about to connect.
GRACE = 3.0


def state_dir():
    from . import log as log_mod  # looked up each time: tests move it
    return log_mod.STATE_DIR


def socket_name(role="background"):
    """An abstract socket (no file, no path length limit) per role, user and
    state folder, like the locks. Abstract sockets have no permissions, so both
    ends check that the other is the same user. The Frame's desktop has its
    own runtime folder but shares these with the Steam session."""
    folder = zlib.crc32(str(state_dir()).encode())
    return f"\0fuelcell-frame-voice-{role}-{os.getuid()}-{folder:08x}"


def place():
    """Where this copy shows windows: the Steam session or the Frame's desktop
    (each has its own display and runtime folder)."""
    return "|".join(os.environ.get(k, "") for k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"))


# -- one copy per role ------------------------------------------------------------
_held = {}


def hold_lock(role):
    """Take the lock for this copy's role ("window" or "background"), held
    until the process ends. False if another copy has it."""
    if role in _held:
        return True
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder / f"{role}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    _held[role] = fd
    return True


def wait_for_lock(role, seconds):
    end = time.monotonic() + seconds
    while not hold_lock(role):
        if time.monotonic() > end:
            return False
        time.sleep(0.1)
    return True


def release_lock(role):
    fd = _held.pop(role, None)
    if fd is not None:
        os.close(fd)


# -- messages: one JSON object per line -------------------------------------------
def send(sock, message):
    sock.sendall(json.dumps(message).encode() + b"\n")


def receive(stream):
    line = stream.readline(1 << 20)
    if not line:
        raise OSError("connection closed")
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("not a message")
    return message


def stop_listening(sock):
    """Close a listening socket. Shutting it down first wakes the thread
    blocked in accept(), which would otherwise keep it open and answering."""
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


def same_user(conn):
    try:
        creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except OSError:
        return False
    return struct.unpack("3i", creds)[1] == os.getuid()


class Server:
    """The background copy's end. Calls on_window(True) when a window's copy
    connects, on_window(False) when the last one is gone, or on_quit() instead
    when the window asked to quit the app altogether."""

    def __init__(self, on_window, on_quit, status=dict):
        self.on_window, self.on_quit, self.status = on_window, on_quit, status
        self.last_text = ""
        self.windows = 0
        self._lock = threading.Lock()
        self._conns = set()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(socket_name())
        self.sock.listen(4)

    def start(self):
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        stop_listening(self.sock)
        for conn in list(self._conns):  # windows notice and take over
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def has_window(self):
        return self.windows > 0

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return  # closed
            if same_user(conn):
                threading.Thread(target=self._serve, args=(conn,), daemon=True).start()
            else:
                conn.close()

    def _serve(self, conn):
        window = quit_all = False
        self._conns.add(conn)
        try:
            conn.settimeout(5)
            stream = conn.makefile("rb")
            hello = receive(stream).get("hello")
            if hello == "status":
                send(conn, {"pid": os.getpid(), "windows": self.windows, **self.status()})
                return
            if hello != "window":
                return
            with self._lock:
                self.windows += 1
                window = True
                if self.windows == 1:
                    self.on_window(True)
            send(conn, {"last_text": self.last_text})
            conn.settimeout(None)
            while True:
                message = receive(stream)
                if isinstance(message.get("last_text"), str):
                    self.last_text = message["last_text"]
                if message.get("quit"):
                    quit_all = True
        except (OSError, ValueError):
            pass  # the usual way a window's copy goes: the connection ends
        finally:
            self._conns.discard(conn)
            conn.close()
            if window:
                with self._lock:
                    self.windows -= 1
                    if quit_all:
                        self.on_quit()
                    elif self.windows == 0:
                        self.on_window(False)


def _connect(timeout, role="background"):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_name(role))
    except OSError:
        sock.close()
        return None
    if not same_user(sock):
        sock.close()
        return None
    return sock


class Link:
    """The window copy's connection to the background copy. While it's open,
    the background copy leaves everything to the window's copy."""

    def __init__(self, sock, last_text):
        self.sock = sock
        self.last_text = last_text
        self.alive = True
        self._send_lock = threading.Lock()

    @classmethod
    def open(cls, timeout=2.0):
        """Connect to the background copy; None if none is running."""
        sock = _connect(timeout)
        if sock is None:
            return None
        try:
            send(sock, {"hello": "window", "pid": os.getpid()})
            reply = receive(sock.makefile("rb"))
        except (OSError, ValueError):
            sock.close()
            return None
        sock.settimeout(None)
        last = reply.get("last_text")
        return cls(sock, last if isinstance(last, str) else "")

    def watch(self, on_lost):
        """Call on_lost() (from another thread) when the background copy ends."""
        def run():
            try:
                while self.sock.recv(4096):
                    pass
            except OSError:
                pass
            self.alive = False
            on_lost()
        threading.Thread(target=run, daemon=True).start()

    def send_last(self, text):
        self._send({"last_text": text})

    def quit(self):
        """Ask the background copy to quit too (when the window's connection ends)."""
        self._send({"quit": True})

    def _send(self, message):
        try:
            with self._send_lock:
                send(self.sock, message)
        except OSError:
            self.alive = False

    def close(self):
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def status(timeout=2.0):
    """What the background copy says about itself, or None if none is running."""
    sock = _connect(timeout)
    if sock is None:
        return None
    try:
        send(sock, {"hello": "status"})
        return receive(sock.makefile("rb"))
    except (OSError, ValueError):
        return None
    finally:
        sock.close()


# -- opening the app while its window copy runs ------------------------------------
class WindowServer:
    """The window copy's end: a copy opened while it runs asks it to show its
    window. If that copy was opened in the other session (the Steam session or
    the Frame's desktop), this one quits so the window can open there, where
    the user is. on_show() and on_move() are called from a worker thread."""

    def __init__(self, on_show, on_move, here=None):
        self.on_show, self.on_move = on_show, on_move
        self.here = place() if here is None else here
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(socket_name("window"))
        self.sock.listen(4)

    def start(self):
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        stop_listening(self.sock)

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                if not same_user(conn):
                    continue
                try:
                    conn.settimeout(3)
                    there = receive(conn.makefile("rb")).get("show")
                    move = isinstance(there, str) and there != self.here
                    send(conn, {"answer": "bye" if move else "shown"})
                except (OSError, ValueError):
                    continue
            if move:
                log.info("opened in another session (%s); moving the window there", there)
                self.on_move()
            else:
                self.on_show()


def ask_window(here=None, timeout=3.0):
    """Ask the running window copy to show its window: "shown", "bye" (it's
    quitting so the window can open here instead), or "" (no answer)."""
    sock = _connect(timeout, "window")
    if sock is None:
        return ""
    try:
        send(sock, {"show": place() if here is None else here})
        answer = receive(sock.makefile("rb")).get("answer")
        return answer if isinstance(answer, str) else ""
    except (OSError, ValueError):
        return ""
    finally:
        sock.close()


# -- the background copy ------------------------------------------------------------
class Background:
    """The engine and the controller shortcuts, without a window, stepping
    aside while the window's copy runs."""

    def __init__(self, cfg, engine_cls, vr_cls=None, copy=lambda text: None,
                 on_quit=lambda: None, grace=GRACE):
        if vr_cls is None:
            from .vr import SteamVR as vr_cls
        self.cfg = cfg
        self.copy = copy
        self.on_quit = on_quit
        self.grace = grace
        self.ready = False  # the engine loaded and the grace time is over
        self.engine = engine_cls(cfg, on_state=self._on_state, on_transcript=self._on_transcript)
        self.vr = vr_cls(self.engine.hotkey, cfg["controller_preset"], edits=cfg["edit_shortcuts"],
                         in_games=cfg["in_games"], autolaunch=cfg["autostart"])
        self.vr.hold = True
        self.engine.paused = True
        self.server = Server(self._window_changed, self._quit, self._status)

    def start(self):
        self.server.start()
        self.vr.start()
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        started = time.monotonic()
        self.engine.load()
        if self.engine.state != "error" and self.cfg.get("keyboard_hotkeys"):
            self.engine.start_hotkeys()
        time.sleep(max(0.0, started + self.grace - time.monotonic()))
        self.ready = True
        with self.server._lock:
            self._apply()

    def _window_changed(self, open_now):
        """From the server, under its lock."""
        if open_now:
            log.info("the window is open; it has the shortcuts until it closes")
        else:
            log.info("the window closed; the shortcuts run in the background again")
            self._reload_settings()
        self._apply()

    def _apply(self):
        hold = not self.ready or self.server.has_window()
        if hold and not self.engine.paused:
            self.engine.cancel_talking()
        self.engine.paused = hold
        self.vr.hold = hold

    def _reload_settings(self):
        """The window may have changed the settings."""
        new = config_mod.load()
        model = self.cfg["model"]
        self.cfg.update(new)
        self.vr.set_preset(new["controller_preset"])
        self.vr.set_options(edits=new["edit_shortcuts"], in_games=new["in_games"])
        if new["model"] != model and self.engine.transcriber is not None:
            self.engine.reload_model(new["model"])

    def _on_state(self, state, message):
        self.vr.show_state(state, message)

    def _on_transcript(self, text):
        self.server.last_text = text
        self.copy(text)

    def _status(self):
        return {"engine": self.engine.state, "shortcuts": self.vr.status,
                "summary": self.vr.summary, "display": os.environ.get("DISPLAY", "")}

    def _quit(self):
        log.info("quit from the window")
        self.on_quit()


def x_display_works(name, timeout=3.0):
    """True if the X display answers (with whatever authorisation we have)."""
    result = []

    def probe():
        try:
            xcb = ctypes.CDLL("libxcb.so.1")
            xcb.xcb_connect.restype = ctypes.c_void_p
            xcb.xcb_connect.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
            xcb.xcb_connection_has_error.argtypes = [ctypes.c_void_p]
            xcb.xcb_disconnect.argtypes = [ctypes.c_void_p]
            conn = xcb.xcb_connect(name.encode(), None)
            result.append(xcb.xcb_connection_has_error(conn) == 0)
            xcb.xcb_disconnect(conn)
        except OSError:
            result.append(False)

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(timeout)
    return bool(result and result[0])


def pick_display():
    """The X display for the clipboard: our own, or else the Steam session's
    (:0), which a background start may not have been told about. None if
    neither answers; then there's no clipboard, but typing still works."""
    names = [os.environ["DISPLAY"]] if os.environ.get("DISPLAY") else []
    if ":0" not in names and os.path.exists("/tmp/.X11-unix/X0"):
        names.append(":0")
    for name in names:
        if x_display_works(name):
            return name
    return None


def run_background(cfg, engine_cls):
    """The background copy's main. Exits quietly if one is already running."""
    if not hold_lock("background"):
        log.info("already running in the background")
        return 0
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C works when run by hand
    display = pick_display()
    if display:
        os.environ["DISPLAY"] = display
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    else:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    log.info("background copy: %s", f"clipboard on display {display}" if display
             else "no display answers, so no clipboard (typing still works)")

    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QGuiApplication

    class Bridge(QObject):  # from worker threads to the Qt thread
        copy = Signal(str)
        quit = Signal()

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    app.setApplicationName("frame-voice")
    app.setQuitOnLastWindowClosed(False)
    bridge = Bridge()
    bridge.copy.connect(lambda text: QGuiApplication.clipboard().setText(text))
    bridge.quit.connect(app.quit)
    work = Background(cfg, engine_cls, copy=bridge.copy.emit, on_quit=bridge.quit.emit)
    work.start()
    code = app.exec()
    work.server.close()
    return code
