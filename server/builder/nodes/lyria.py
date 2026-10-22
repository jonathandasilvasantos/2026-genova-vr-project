"""Gemini Lyria music node (text -> ~30 s instrumental mp3). Key: GEMINI_API_KEY (or GOOGLE_API_KEY)."""
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nodes"))
from library import CapExceeded, check_cap, record  # noqa: E402
CACHE = Path(os.environ.get("GENOVA_VR_CACHE", ROOT / ".cache")) / "music"
MODEL = "lyria-3-pro-preview"  # ~2 minute tracks; the clip model gives 30 s with a built-in ending
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SAFE_SUFFIX = (" A long evolving piece of about 2 minutes with steady, even energy from start to finish: no dramatic build-ups, no big ending, no fade-out, no sudden stops."
               " Instrumental only, no vocals, no lyrics, unobtrusive, suitable as quiet background music under a narrator.")


class MusicError(RuntimeError):
    pass


def generate(prompt: str, model: str = MODEL) -> bytes:
    from api_budget import block_unmetered
    block_unmetered("Lyria music")
    text = prompt.strip() + SAFE_SUFFIX
    digest = hashlib.sha256(f"{model}|{text}".encode()).hexdigest()
    cached = CACHE / f"{digest}.mp3"
    if cached.exists():
        record("lyria", "music", cached=True, detail=prompt[:100])
        return cached.read_bytes()
    try:
        check_cap("lyria")
    except CapExceeded as e:
        raise MusicError(str(e)) from e
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise MusicError("set GEMINI_API_KEY")
    body = json.dumps({"contents": [{"parts": [{"text": text}]}]}).encode()
    last = None
    for attempt in range(4):
        req = urllib.request.Request(URL.format(model=model), data=body,
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.load(r)
            for cand in data.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    inline = part.get("inlineData")
                    if inline and inline.get("mimeType", "").startswith("audio/"):
                        raw = base64.b64decode(inline["data"])
                        CACHE.mkdir(parents=True, exist_ok=True)
                        cached.write_bytes(raw)
                        record("lyria", "music", cached=False, detail=prompt[:100])
                        return raw
            last = "no audio in reply"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (400, 401, 403):
                raise MusicError(f"Lyria request refused ({last})") from e
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(e)
        time.sleep(2 ** attempt)
    raise MusicError(f"Lyria failed: {last}")
