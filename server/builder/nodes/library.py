"""Shared asset library + cost ledger: the way Genova VR avoids paying twice for the same thing.

Library kinds: music, sfx, models. Every entry has an id, a file and metadata. Experiences REUSE entries
(the director/composer/sound designer are shown the catalog and told to prefer it), new paid results are
stored here, and every paid call (or avoided call) is written to the ledger with a cost estimate.

Layout under GENOVA_VR_LIBRARY (default ./library):
  music/index.json  music/<id>.mp3     sfx/index.json  sfx/<id>.mp3     models/index.json  models/<id>.glb
  ledger.jsonl                         one JSON line per paid call or cache hit
"""
import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = Path(os.environ.get("GENOVA_VR_LIBRARY", ROOT / "library"))

# Rough per-call cost in USD (override with env COST_<PROVIDER>). Estimates, shown as such in the UI.
EST_COST = {"lyria": 0.08, "elevenlabs": 0.02, "cloudrun_gpu": 0.15, "openrouter": 0.0}
# Daily ceilings for non-cached paid calls (override with env <PROVIDER>_MAX_PER_DAY).
DEFAULT_CAPS = {"lyria": 12, "elevenlabs": 120, "cloudrun_gpu": 60}


class CapExceeded(RuntimeError):
    pass


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text.lower())).strip()


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


class Library:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or LIB)

    def _index(self, kind) -> Path:
        return self.root / kind / "index.json"

    def list(self, kind) -> dict:
        try:
            return json.loads(self._index(kind).read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def get(self, kind, entry_id):
        """Returns (path, meta) or None."""
        meta = self.list(kind).get(entry_id)
        if not meta:
            return None
        f = self.root / kind / meta["file"]
        return (f, meta) if f.exists() else None

    def put(self, kind, entry_id, data: bytes, ext: str, **meta):
        fname = f"{entry_id}.{ext}"
        d = self.root / kind
        with _locked(self._index(kind)):
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / (fname + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(d / fname)
            idx = self.list(kind)
            idx[entry_id] = {"file": fname, "bytes": len(data), "created": idx.get(entry_id, {}).get("created", time.time()),
                             "uses": idx.get(entry_id, {}).get("uses", 0), **meta}
            self._index(kind).write_text(json.dumps(idx, indent=1, ensure_ascii=False))
        return d / fname

    def touch(self, kind, entry_id):
        """Count a reuse."""
        with _locked(self._index(kind)):
            idx = self.list(kind)
            if entry_id in idx:
                idx[entry_id]["uses"] = idx[entry_id].get("uses", 0) + 1
                idx[entry_id]["last_used"] = time.time()
                self._index(kind).write_text(json.dumps(idx, indent=1, ensure_ascii=False))

    def catalog(self, kind, limit=60):
        """Compact list for prompts: [{"id", "description"}]."""
        items = sorted(self.list(kind).items(), key=lambda kv: -kv[1].get("uses", 0))[:limit]
        return [{"id": k, "description": (v.get("description") or v.get("brief") or v.get("prompt") or "")[:200]} for k, v in items]


# --------------------------------------------------------------------------- ledger
LEDGER = LIB / "ledger.jsonl"


def _est(provider):
    try:
        return float(os.environ.get(f"COST_{provider.upper()}", EST_COST.get(provider, 0.0)))
    except ValueError:
        return EST_COST.get(provider, 0.0)


def record(provider, action, cached=False, cost=None, detail=""):
    """Append a ledger line. `cost` is exact when the provider reports it, else estimated."""
    exact = cost is not None
    row = {"t": round(time.time(), 1), "provider": provider, "action": action, "cached": bool(cached),
           "cost": round(cost if exact else _est(provider), 4), "exact": exact, "est_if_paid": round(_est(provider), 4),
           "detail": detail[:120]}
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(row) + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        pass


def _rows(since=0):
    try:
        for line in LEDGER.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r["t"] >= since:
                yield r
    except OSError:
        return


def paid_today(provider):
    since = time.time() - 24 * 3600
    return sum(1 for r in _rows(since) if r["provider"] == provider and not r["cached"])


def check_cap(provider):
    cap = int(os.environ.get(f"{provider.upper()}_MAX_PER_DAY", DEFAULT_CAPS.get(provider, 10**9)))
    if paid_today(provider) >= cap:
        raise CapExceeded(f"{provider}: daily limit of {cap} paid calls reached")


def summary(days=30):
    since = time.time() - days * 86400
    out = {}
    for r in _rows(since):
        s = out.setdefault(r["provider"], {"paid_calls": 0, "cache_hits": 0, "spent": 0.0, "spent_exact": True, "saved": 0.0})
        if r["cached"]:
            s["cache_hits"] += 1
            s["saved"] += r["est_if_paid"]
        else:
            s["paid_calls"] += 1
            s["spent"] += r["cost"]
            s["spent_exact"] = s["spent_exact"] and r["exact"]
    for s in out.values():
        s["spent"], s["saved"] = round(s["spent"], 2), round(s["saved"], 2)
    return out
