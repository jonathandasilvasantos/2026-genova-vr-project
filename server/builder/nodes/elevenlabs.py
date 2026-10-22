"""ElevenLabs sound-effects node (text -> short mp3). Key: ELEVEN_API_KEY (or ELEVENLABS_API_KEY)."""
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
CACHE = Path(os.environ.get("GENOVA_VR_CACHE", ROOT / ".cache")) / "sfx"
URL = "https://api.elevenlabs.io/v1/sound-generation?output_format=mp3_44100_64"


class SfxError(RuntimeError):
    pass


def _key():
    # Prefer the key that carries the sound_generation permission.
    for name in ("ELEVEN_API_KEY", "ELEVENLABS_API_KEY"):
        if os.environ.get(name):
            yield os.environ[name]


def generate(prompt: str, duration: float = 4.0, loop: bool = False, influence: float = 0.5) -> bytes:
    from api_budget import block_unmetered
    block_unmetered("ElevenLabs sound effects")
    duration = max(0.5, min(30.0, duration))
    body = {"text": prompt, "duration_seconds": duration, "prompt_influence": influence}
    if loop:
        body["loop"] = True
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    cached = CACHE / f"{digest}.mp3"
    if cached.exists():
        record("elevenlabs", "sfx", cached=True, detail=prompt[:100])
        return cached.read_bytes()
    try:
        check_cap("elevenlabs")
    except CapExceeded as e:
        raise SfxError(str(e)) from e
    keys = list(_key())
    if not keys:
        raise SfxError("set ELEVEN_API_KEY")
    last = None
    for key in keys:
        for attempt in range(3):
            req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                         headers={"xi-api-key": key, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                CACHE.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(data)
                record("elevenlabs", "sfx", cached=False, detail=prompt[:100])
                return data
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code in (401, 403):
                    break  # wrong key / missing permission: try the next key
                if e.code == 422:
                    raise SfxError(f"prompt rejected: {prompt!r}") from e
                time.sleep(2 ** attempt)
            except (urllib.error.URLError, TimeoutError) as e:
                last = str(e)
                time.sleep(2 ** attempt)
    raise SfxError(f"ElevenLabs failed: {last}")
