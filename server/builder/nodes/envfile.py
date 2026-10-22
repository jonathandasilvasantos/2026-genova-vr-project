"""Load KEY=VALUE pairs from a local .env file without overriding the environment.

The .env file is git-ignored. Keep secrets there, never in code or in tracked files.
"""
import os
from pathlib import Path


def load(path) -> int:
    path = Path(path)
    if not path.is_file():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded
