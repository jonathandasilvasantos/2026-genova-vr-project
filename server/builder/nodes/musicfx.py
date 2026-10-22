"""Post-processing for generated music so it sits well under narration and loops without a 'stop'.

Generated tracks end with a natural fade-out and may start with near-silence. We trim both, then
loudness-normalise so the per-track `volume` values mean the same thing across tracks.
"""
import subprocess

import numpy as np

SR = 22050
TARGET_RMS = 0.075  # about -22.5 dBFS, comfortable as an underscore at volume 0.15


def _decode(data: bytes) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1", "-ar", str(SR), "pipe:1"],
                       input=data, capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype=np.float32)


def _encode(x: np.ndarray, stereo_src: bytes | None = None) -> bytes:
    p = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0",
                        "-c:a", "libmp3lame", "-b:a", "96k", "-f", "mp3", "pipe:1"],
                       input=x.astype(np.float32).tobytes(), capture_output=True, check=True)
    return p.stdout


def process(data: bytes) -> tuple[bytes, dict]:
    """Returns (mp3 bytes, info). Falls back to the original bytes if ffmpeg is unavailable."""
    try:
        x = _decode(data)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return data, {"processed": False}
    win = SR // 2
    n = len(x) // win
    if n < 6:
        return data, {"processed": False}
    rms = np.array([np.sqrt(np.mean(x[i * win:(i + 1) * win] ** 2)) for i in range(n)])
    ref = float(np.median(rms))
    keep = np.where(rms >= 0.5 * ref)[0]
    start = int(keep[0]) * win if len(keep) else 0
    end = (int(keep[-1]) + 1) * win if len(keep) else len(x)
    # never cut more than a third from either side, and keep at least 20 s
    start = min(start, len(x) // 6)
    end = max(end, len(x) * 2 // 3)
    y = x[start:end].copy()
    # gentle edges so that the player's crossfades start from a clean signal
    fi = min(len(y) // 20, SR // 4)
    y[:fi] *= np.linspace(0, 1, fi)
    gain = TARGET_RMS / max(float(np.sqrt(np.mean(y ** 2))), 1e-5)
    y = np.clip(y * min(gain, 4.0), -0.98, 0.98)
    return _encode(y), {"processed": True, "seconds": round(len(y) / SR, 1), "trimmed_head_s": round(start / SR, 1),
                        "trimmed_tail_s": round((len(x) - end) / SR, 1), "gain": round(float(gain), 2)}
