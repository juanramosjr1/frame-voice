"""Types text into whatever text box has focus.

Text goes out through a virtual USB keyboard, so anything that accepts a real
keyboard accepts it: the Steam store and client, browsers, desktop apps.
Assumes a US keyboard layout.
"""

import time

from .linux_input import KEY, UInputKeyboard

# char -> (key name, needs shift)
_KEYMAP = {}
for _c in "abcdefghijklmnopqrstuvwxyz":
    _KEYMAP[_c] = (_c.upper(), False)
    _KEYMAP[_c.upper()] = (_c.upper(), True)
for _c in "1234567890":
    _KEYMAP[_c] = (_c, False)
for _c, _name in zip(")!@#$%^&*(", "0123456789"):
    _KEYMAP[_c] = (_name, True)
for _plain, _shifted, _name in [
    ("-", "_", "MINUS"), ("=", "+", "EQUAL"), ("[", "{", "LEFTBRACE"),
    ("]", "}", "RIGHTBRACE"), ("\\", "|", "BACKSLASH"), (";", ":", "SEMICOLON"),
    ("'", '"', "APOSTROPHE"), (",", "<", "COMMA"), (".", ">", "DOT"),
    ("/", "?", "SLASH"), ("`", "~", "GRAVE"),
]:
    _KEYMAP[_plain] = (_name, False)
    _KEYMAP[_shifted] = (_name, True)
_KEYMAP.update({" ": ("SPACE", False), "\n": ("ENTER", False), "\t": ("TAB", False)})

# Characters Whisper likes to emit that a US keyboard can't type directly.
_REPLACEMENTS = {
    "’": "'", "‘": "'", "“": '"', "”": '"',
    "—": "-", "–": "-", "…": "...", " ": " ",
}

SHORTCUTS = {
    "copy": ["LEFTCTRL", "C"],
    "paste": ["LEFTCTRL", "V"],
    "cut": ["LEFTCTRL", "X"],
    "select_all": ["LEFTCTRL", "A"],
    "undo": ["LEFTCTRL", "Z"],
    "enter": ["ENTER"],
    "backspace": ["BACKSPACE"],
    "delete_word": ["LEFTCTRL", "BACKSPACE"],
    "space": ["SPACE"],
}


def to_keystrokes(text):
    """Turn text into (keycode, shift) pairs, dropping anything untypeable."""
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    return [(KEY[_KEYMAP[c][0]], _KEYMAP[c][1]) for c in text if c in _KEYMAP]


def all_codes():
    codes = {KEY[name] for name, _ in _KEYMAP.values()}
    codes |= {KEY[name] for combo in SHORTCUTS.values() for name in combo}
    codes.add(KEY["LEFTSHIFT"])
    return codes


class Typist:
    def __init__(self, device=None, delay=0.006):
        self._dev = device or UInputKeyboard(all_codes())
        self._delay = delay

    def _tap(self, code):
        self._dev.emit(code, 1)
        self._dev.emit(code, 0)
        time.sleep(self._delay)

    def type_text(self, text):
        shift = KEY["LEFTSHIFT"]
        for code, shifted in to_keystrokes(text):
            if shifted:
                self._dev.emit(shift, 1)
            self._tap(code)
            if shifted:
                self._dev.emit(shift, 0)

    def shortcut(self, name):
        codes = [KEY[k] for k in SHORTCUTS[name]]
        for code in codes:
            self._dev.emit(code, 1)
        for code in reversed(codes):
            self._dev.emit(code, 0)
        time.sleep(self._delay)

    def close(self):
        self._dev.close()
