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
    text = Transcriber("base.en").transcribe(str(wav)).lower()
    print("heard:", text)
    # base.en hears this robot voice's "Hello" as "the low" with old and new settings alike.
    for word in ("world", "typing", "test"):
        assert word in text


SENTENCES = [
    "Hello world, this is a voice typing test.",
    "Open the Steam store and search for games.",
    "I did say do it, but you didn't listen.",
    "Can you send me the link to that video later tonight?",
    "Please turn the volume down a little bit.",
    "We should meet at seven thirty near the coffee shop.",
]


# (what's said, how the word list spells it)
NAMES = [
    ("My company is called fuel cell.", "fuelCell"),
    ("Send the link to Kaylee tonight.", "Kayleigh"),
    ("Tonight I am playing Half Life Alyx.", "Alyx"),
]
MY_WORDS = ", ".join(spelled for _said, spelled in NAMES)


def word_errors(ref, heard):
    """Word-level edit distance (substitutions, missing and extra words)."""
    import re

    def words(t):
        return re.sub(r"[^a-z0-9 ]", "", t.lower().replace("-", " ")).split()

    r, h = words(ref), words(heard)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[len(h)], len(r)


def test_dictation_is_more_accurate_than_before(tmp_path):
    """Measure the 0.4.1 settings against the old ones on the default model:
    clear, quiet (far from the mic) and noisy speech, plus silence. Also
    check that a word list doesn't make everything else worse."""
    if not shutil.which("espeak-ng"):
        pytest.skip("espeak-ng not installed")
    import numpy as np

    from frame_voice.voice import Transcriber, load_wav

    tx = Transcriber("base.en")
    clips = []
    for i, sentence in enumerate(SENTENCES):
        for voice, speed in (("en-us", 150), ("en+m3", 175), ("en+f3", 135)):
            wav = tmp_path / f"{i}-{voice}.wav"
            subprocess.run(["espeak-ng", "-v", voice, "-s", str(speed), "-w", str(wav),
                            sentence], check=True)
            audio = load_wav(str(wav))
            noise = np.random.default_rng(i).normal(0, 0.02, len(audio)).astype(np.float32)
            clips += [(sentence, "clear", audio), (sentence, "quiet", audio * 0.04),
                      (sentence, "noisy", audio + noise)]
    old = dict(beam=1, boost=False, gentle_vad=False, drop_phantoms=False)
    variants = {"old": old, "new": {}, "new with a word list": dict(words=MY_WORDS)}
    score = {}
    for name, options in variants.items():
        errors, total, start, examples = {}, {}, time.monotonic(), []
        for sentence, kind, audio in clips:
            heard = tx.decode(audio, **options)
            e, n = word_errors(sentence, heard)
            errors[kind] = errors.get(kind, 0) + e
            total[kind] = total.get(kind, 0) + n
            if e and kind == "clear" and len(examples) < 2:
                examples.append(heard)
        took = (time.monotonic() - start) / len(clips)
        rate = 100 * sum(errors.values()) / sum(total.values())
        parts = ", ".join(f"{k} {100 * errors[k] / total[k]:.1f}%" for k in errors)
        print(f"::notice title=accuracy {name}::wrong words {rate:.1f}% ({parts}), "
              f"{took:.2f} s per dictation, e.g. {examples}")
        score[name] = rate
    rng = np.random.default_rng(7)
    hush = [rng.normal(0, 0.004 * (k + 1), 32000).astype(np.float32) for k in range(6)]
    phantom = {name: sum(bool(tx.decode(a, **options)) for a in hush)
               for name, options in variants.items()}
    print("::notice title=silence::typed something for "
          + ", ".join(f"{n}/6 silent clips ({name})" for name, n in phantom.items()))
    assert score["new"] <= score["old"]
    assert score["new with a word list"] <= score["new"] + 2.0
    assert phantom["new"] <= phantom["old"]
    assert phantom["new with a word list"] <= phantom["old"]


def test_my_words_are_spelled_my_way(tmp_path):
    """Names on the word list come out spelled the list's way."""
    if not shutil.which("espeak-ng"):
        pytest.skip("espeak-ng not installed")
    from frame_voice.voice import Transcriber, load_wav

    tx = Transcriber("base.en")
    right = {"without": 0, "with": 0}
    heard = {"without": [], "with": []}
    for i, (said, spelled) in enumerate(NAMES):
        for voice, speed in (("en-us", 150), ("en+m3", 170)):
            wav = tmp_path / f"name{i}-{voice}.wav"
            subprocess.run(["espeak-ng", "-v", voice, "-s", str(speed), "-w", str(wav), said],
                           check=True)
            audio = load_wav(str(wav))
            for key, words in (("without", ""), ("with", MY_WORDS)):
                text = tx.decode(audio, words=words)
                right[key] += spelled in text
                heard[key].append(text)
    total = 2 * len(NAMES)
    for key in ("without", "with"):
        print(f"::notice title=my words, {key} the list::spelled right {right[key]}/{total}: "
              f"{heard[key]}")
    assert right["with"] >= total - 2 and right["with"] > right["without"]


def test_silence_types_nothing(tmp_path):
    import wave

    import numpy as np

    from frame_voice.voice import Transcriber

    hush = tmp_path / "hush.wav"
    with wave.open(str(hush), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.random.default_rng(1).normal(0, 30, 32000).astype("<i2").tobytes())
    assert Transcriber("base.en").transcribe(str(hush)) == ""
