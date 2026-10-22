"""Narration with Kokoro-82M, an open-weights text-to-speech model.

Model: hexgrad/Kokoro-82M (weights and code under the Apache License 2.0).
Runs locally on CPU or GPU through the `kokoro` Python package. English only.

Interface used by the builder and the server:
    client = TTSClient(cache_dir=...)
    wav_bytes = client.speak(text, language="en", voice="af_heart", speed=1.0)
    seconds = wav_duration(wav_bytes)
"""
import hashlib
import io
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 24000          # Kokoro output rate
DEFAULT_VOICE = "af_heart"   # American English female voice shipped with Kokoro
LANG_CODE = "a"              # American English
MAX_CHARS = 2000             # keeps each synthesis call short and predictable


class TTSError(RuntimeError):
    pass


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _to_wav(samples: np.ndarray) -> bytes:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class TTSClient:
    _pipeline = None

    def __init__(self, cache_dir=None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _load(cls):
        if cls._pipeline is None:
            try:
                from kokoro import KPipeline
            except ImportError as e:
                raise TTSError("install the 'kokoro' package (see server/README.md)") from e
            cls._pipeline = KPipeline(lang_code=LANG_CODE)
        return cls._pipeline

    def speak(self, text, language="en", voice=None, speed=1.0) -> bytes:
        if not language.lower().startswith("en"):
            raise TTSError(f"Kokoro narration supports English only, not {language!r}")
        text = (text or "").strip()
        if not text:
            raise TTSError("empty narration text")
        if len(text) > MAX_CHARS:
            raise TTSError(f"narration text longer than {MAX_CHARS} characters")
        voice = voice or DEFAULT_VOICE
        key = hashlib.sha256(f"{voice}|{speed}|{text}".encode("utf-8")).hexdigest()
        cached = self.cache_dir / f"{key}.wav" if self.cache_dir else None
        if cached and cached.exists():
            return cached.read_bytes()
        pipeline = self._load()
        chunks = [audio for _, _, audio in pipeline(text, voice=voice, speed=speed)]
        if not chunks:
            raise TTSError("Kokoro returned no audio")
        data = _to_wav(np.concatenate([np.asarray(c, dtype=np.float32) for c in chunks]))
        if cached:
            cached.write_bytes(data)
        return data
