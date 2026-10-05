"""Keep keyboard focus on the user's text box, not our window.

Clicking our window can make it the active window (KWin ignores Qt's "never
take focus" hint), and then typed text would land in our own window instead
of the text box the user picked. Two fixes for KDE (the SteamOS desktop):

1. A KWin window rule that forces "accept focus: no" for our window, the
   way on-screen keyboards do it. Then clicking our buttons never moves focus.
2. As a backup, right before sending keys, hand focus back to the window the
   user was in (the topmost normal window that isn't ours), using a tiny
   KWin script over D-Bus, or plain X11 when KWin isn't there.
"""

import os
import shutil
import subprocess
import tempfile

from .log import log

OUR_CLASS = "frame-voice"
OUR_TITLE = "fuelCell Voice Typing"

# Activates the topmost visible, normal window that isn't ours.
KWIN_SCRIPT = """
(function () {
    var plasma6 = typeof workspace.stackingOrder !== "undefined" && "activeWindow" in workspace;
    var list = plasma6 ? workspace.stackingOrder : workspace.clientList();
    for (var i = list.length - 1; i >= 0; i--) {
        var w = list[i];
        if (!w || w.minimized || !w.normalWindow || w.skipTaskbar) continue;
        var cls = String(w.resourceClass || "").toLowerCase();
        var title = String(w.caption || "");
        if (cls.indexOf("%(cls)s") !== -1 || title.indexOf("%(title)s") !== -1) continue;
        if (plasma6) { workspace.activeWindow = w; } else { workspace.activeClient = w; }
        break;
    }
})();
""" % {"cls": OUR_CLASS, "title": OUR_TITLE}

_NAME = "frame-voice-refocus"


def _dbus(*args):
    """Call KWin over D-Bus with dbus-send; returns stdout or None."""
    tool = shutil.which("dbus-send")
    if not tool or not os.environ.get("DBUS_SESSION_BUS_ADDRESS", "x"):
        return None
    try:
        out = subprocess.run([tool, "--session", "--print-reply", "--dest=org.kde.KWin", *args],
                             capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        log.info("kwin dbus %s failed: %s", args[:2], out.stderr.strip()[:200])
        return None
    return out.stdout


def activate_previous_kwin():
    """Ask KWin to activate the window under ours. Returns True if KWin ran it."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", prefix="frame-voice-", delete=False) as f:
        f.write(KWIN_SCRIPT)
        path = f.name
    try:
        _dbus("/Scripting", "org.kde.kwin.Scripting.unloadScript", f"string:{_NAME}")
        out = _dbus("/Scripting", "org.kde.kwin.Scripting.loadScript",
                    f"string:{path}", f"string:{_NAME}")
        if out is None:
            return False
        script_id = out.strip().split()[-1]
        # Plasma 6 path first, then Plasma 5.
        ran = (_dbus(f"/Scripting/Script{script_id}", "org.kde.kwin.Script.run") is not None
               or _dbus(f"/{script_id}", "org.kde.kwin.Script.run") is not None)
        _dbus("/Scripting", "org.kde.kwin.Scripting.unloadScript", f"string:{_NAME}")
        return ran
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def activate_previous_x11(our_window_ids=()):
    """X11 / XWayland fallback: ask the window manager (EWMH) to activate the
    topmost window that isn't ours. Returns True if a request was sent."""
    import ctypes
    import ctypes.util

    name = ctypes.util.find_library("X11")
    if not name or not os.environ.get("DISPLAY"):
        return False
    x = ctypes.CDLL(name)
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XDefaultRootWindow.restype = ctypes.c_ulong
    x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x.XInternAtom.restype = ctypes.c_ulong
    x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x.XGetWindowProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
        ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_void_p)]
    x.XFree.argtypes = [ctypes.c_void_p]
    x.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
    x.XFlush.argtypes = [ctypes.c_void_p]
    x.XCloseDisplay.argtypes = [ctypes.c_void_p]

    dpy = x.XOpenDisplay(None)
    if not dpy:
        return False
    try:
        root = x.XDefaultRootWindow(dpy)

        def prop(win, atom_name, want_type):
            atom = x.XInternAtom(dpy, atom_name, False)
            actual, fmt = ctypes.c_ulong(), ctypes.c_int()
            n, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
            ok = x.XGetWindowProperty(dpy, win, atom, 0, 4096, False, want_type, ctypes.byref(actual),
                                      ctypes.byref(fmt), ctypes.byref(n), ctypes.byref(after),
                                      ctypes.byref(data))
            if ok != 0 or not data.value:
                return None
            try:
                if fmt.value == 32:
                    return list((ctypes.c_ulong * n.value).from_address(data.value))
                return ctypes.string_at(data.value, n.value)
            finally:
                x.XFree(data)

        XA_WINDOW, XA_ATOM = 33, 4
        stack = prop(root, b"_NET_CLIENT_LIST_STACKING", XA_WINDOW) or []
        normal = x.XInternAtom(dpy, b"_NET_WM_WINDOW_TYPE_NORMAL", False)
        hidden = x.XInternAtom(dpy, b"_NET_WM_STATE_HIDDEN", False)
        target = None
        for win in reversed(stack):
            if win in our_window_ids:
                continue
            types = prop(win, b"_NET_WM_WINDOW_TYPE", XA_ATOM)
            if types and normal not in types:
                continue
            if hidden in (prop(win, b"_NET_WM_STATE", XA_ATOM) or []):
                continue
            cls = (prop(win, b"WM_CLASS", 31) or b"").lower()
            if OUR_CLASS.encode() in cls:
                continue
            target = win
            break
        if target is None:
            return False

        class ClientMessage(ctypes.Structure):
            _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                        ("display", ctypes.c_void_p), ("window", ctypes.c_ulong),
                        ("message_type", ctypes.c_ulong), ("format", ctypes.c_int),
                        ("data", ctypes.c_long * 5)]

        ev = (ctypes.c_char * 192)()  # sizeof(XEvent)
        msg = ClientMessage.from_buffer(ev)
        msg.type = 33  # ClientMessage
        msg.send_event = 1
        msg.window = target
        msg.message_type = x.XInternAtom(dpy, b"_NET_ACTIVE_WINDOW", False)
        msg.format = 32
        msg.data[0] = 2  # source: pager / tool, which window managers honour
        mask = (1 << 20) | (1 << 19)  # SubstructureRedirect | SubstructureNotify
        x.XSendEvent(dpy, root, False, mask, ctypes.addressof(ev))
        x.XFlush(dpy)
        return True
    finally:
        x.XCloseDisplay(dpy)


# -- KWin window rule ------------------------------------------------------------
RULE_GROUP = "frame-voice"
RULE = {
    "Description": "fuelCell Voice Typing: never take keyboard focus",
    "wmclass": OUR_CLASS,
    "wmclassmatch": "1",          # exact match on the window class
    "wmclasscomplete": "false",
    "types": "1",                 # normal windows only, so dialogs still work
    "acceptfocus": "false",
    "acceptfocusrule": "2",       # force
}


class KConfig:
    """Reads and writes ~/.config/kwinrulesrc with KDE's own command-line tools."""

    FILE = "kwinrulesrc"

    def __init__(self):
        self.tools = None
        for version in ("6", "5"):
            write, read = shutil.which(f"kwriteconfig{version}"), shutil.which(f"kreadconfig{version}")
            if write and read:
                self.tools = (write, read)
                break

    def _run(self, args):
        out = subprocess.run(args, capture_output=True, text=True, timeout=5)
        if out.returncode != 0:  # never mistake a failed read for "no rules"
            raise subprocess.SubprocessError(f"{os.path.basename(args[0])}: {out.stderr.strip()[:200]}")
        return out.stdout.strip()

    def get(self, group, key):
        return self._run([self.tools[1], "--file", self.FILE, "--group", group, "--key", key])

    def put(self, group, key, value):
        self._run([self.tools[0], "--file", self.FILE, "--group", group, "--key", key, value])

    def delete(self, group, key):
        self._run([self.tools[0], "--file", self.FILE, "--group", group, "--key", key, "--delete"])


def _rule_list(conf):
    rules = [r for r in conf.get("General", "rules").split(",") if r]
    if not rules:  # older files number their rules 1..count instead
        count = conf.get("General", "count")
        rules = [str(i) for i in range(1, int(count) + 1)] if count.isdigit() else []
    return rules


def _set_rule_list(conf, rules):
    conf.put("General", "rules", ",".join(rules))
    conf.put("General", "count", str(len(rules)))


def install_kwin_rule(conf=None):
    """Add our KWin rule if it isn't there yet. Returns True if it's in place."""
    conf = conf or KConfig()
    if getattr(conf, "tools", True) is None:
        return False
    try:
        rules = _rule_list(conf)
        if RULE_GROUP in rules and all(conf.get(RULE_GROUP, k) == v for k, v in RULE.items()):
            return True
        for key, value in RULE.items():
            conf.put(RULE_GROUP, key, value)
        if RULE_GROUP not in rules:
            _set_rule_list(conf, rules + [RULE_GROUP])
    except (OSError, subprocess.SubprocessError, ValueError) as err:
        log.info("couldn't add the KWin rule: %s", err)
        return False
    _dbus("/KWin", "org.kde.KWin.reconfigure")
    log.info("added KWin rule so our window never takes keyboard focus")
    return True


def remove_kwin_rule(conf=None):
    conf = conf or KConfig()
    if getattr(conf, "tools", True) is None:
        return False
    try:
        rules = _rule_list(conf)
        if RULE_GROUP in rules:
            _set_rule_list(conf, [r for r in rules if r != RULE_GROUP])
        for key in RULE:
            conf.delete(RULE_GROUP, key)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    _dbus("/KWin", "org.kde.KWin.reconfigure")
    return True


def activate_previous(our_window_ids=()):
    """Hand focus to the window under ours. KWin first, then plain X11."""
    if activate_previous_kwin():
        return True
    return activate_previous_x11(our_window_ids)
