"""Record from the headset mic and turn it into text with Whisper (runs locally)."""

import os
import shutil
import signal
import subprocess
import tempfile


class Recorder:
    """Push-to-talk recorder using PipeWire's pw-record (falls back to arecord)."""

    def __init__(self):
        self._proc = None
        self._path = None

    @property
    def recording(self):
        return self._proc is not None

    def start(self):
        if self._proc:
            return
        fd, self._path = tempfile.mkstemp(suffix=".wav", prefix="frame-voice-")
        os.close(fd)
        if shutil.which("pw-record"):
            cmd = ["pw-record", "--rate", "16000", "--channels", "1", self._path]
        elif shutil.which("arecord"):
            cmd = ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", self._path]
        else:
            raise RuntimeError("No microphone recorder found (pw-record or arecord)")
        self._proc = subprocess.Popen(cmd)

    def cancel(self):
        path = self.stop()
        if path and os.path.exists(path):
            os.unlink(path)

    def stop(self):
        """Stop recording and return the path of the WAV file, or None."""
        if not self._proc:
            return None
        self._proc.send_signal(signal.SIGINT)
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None
        return self._path


def load_wav(path, rate=16000):
    """Read a PCM WAV as mono float32 at 16 kHz, the format Whisper expects.

    Done here rather than by faster-whisper's own decoder, which depends on
    PyAV and has broken with new PyAV releases.
    """
    import wave

    import numpy as np

    with wave.open(path, "rb") as w:
        channels, width, src_rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit audio, got {8 * width}-bit")
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if src_rate != rate and len(audio):
        n = int(len(audio) * rate / src_rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    return audio.astype(np.float32)


class Transcriber:
    def __init__(self, model="base.en", language="en"):
        from faster_whisper import WhisperModel

        # int8 on CPU keeps this fast enough on the headset's ARM chip.
        self._model = WhisperModel(model, device="cpu", compute_type="int8")
        self._language = language

    def transcribe(self, wav_path):
        segments, _ = self._model.transcribe(
            load_wav(wav_path), language=self._language, vad_filter=True, beam_size=1
        )
        return " ".join(s.text.strip() for s in segments).strip()
