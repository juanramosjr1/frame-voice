"""End-to-end checks that need real hardware access or downloads.

Run in CI (see .github/workflows/test.yml) with FRAME_VOICE_E2E=1, after
loading the uinput module and installing espeak-ng.
"""

import os
import shutil
import subprocess
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("FRAME_VOICE_E2E") != "1",
                                reason="set FRAME_VOICE_E2E=1 to run")


def test_virtual_keyboard_reaches_the_kernel():
    """Type through /dev/uinput and read the key presses back from evdev."""
    from frame_voice.keyboard import Typist, all_codes
    from frame_voice.linux_input import KEY, KeyWatcher, UInputKeyboard

    dev = UInputKeyboard(all_codes(), name="frame-voice e2e")
    seen = []
    watcher = KeyWatcher(all_codes(), lambda c, down: down and seen.append(c),
                         ignore_name=None, rescan_every=0.2)
    threading.Thread(target=watcher.run, daemon=True).start()
    time.sleep(1.0)
    assert watcher.device_count > 0
    Typist(dev, delay=0.01).type_text("Hi!")
    Typist(dev, delay=0.01).shortcut("paste")
    time.sleep(0.5)
    watcher.stop()
    dev.close()
    k = KEY
    assert seen == [k["LEFTSHIFT"], k["H"], k["I"], k["LEFTSHIFT"], k["1"],
                    k["LEFTCTRL"], k["V"]]


@pytest.mark.skipif(not shutil.which("espeak-ng"), reason="espeak-ng not installed")
def test_speech_becomes_text(tmp_path):
    """Synthesize a sentence, then make sure Whisper hears it."""
    from frame_voice.voice import Transcriber

    wav = tmp_path / "say.wav"
    subprocess.run(["espeak-ng", "-s", "140", "-w", str(wav),
                    "Hello world, this is a voice typing test."], check=True)
    text = Transcriber("tiny.en").transcribe(str(wav)).lower()
    print("heard:", text)
    for word in ("hello", "world", "test"):
        assert word in text
