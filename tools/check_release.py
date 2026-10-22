#!/usr/bin/env python3
"""Refuse to publish if the release contains secrets, local env files or assistant names.

Run from the release root before every commit:  python3 tools/check_release.py
Exit status 0 means clean; 1 means something must be fixed first.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "__pycache__", "build", ".gradle", "vendor"}
BINARY = {".apk", ".wav", ".mp3", ".ogg", ".glb", ".png", ".jpg", ".zip", ".so", ".jar"}

SECRET_PATTERNS = {
    "OpenAI/OpenRouter key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "assigned API key value": re.compile(
        r"(OPENROUTER_API_KEY|ELEVEN_API_KEY|GEMINI_API_KEY|API_KEY|SESSION_SECRET)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{16,}"
    ),
}
ASSISTANT_NAMES = re.compile(r"\b(claude|codex|anthropic|openai codex)\b", re.IGNORECASE)

problems = []
for path in ROOT.rglob("*"):
    rel = path.relative_to(ROOT)
    if any(part in SKIP_DIRS for part in rel.parts) or not path.is_file():
        continue
    if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
        problems.append(f"{rel}: local env file must not be published")
        continue
    if path.suffix.lower() in BINARY or path.name == "check_release.py":
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for label, pattern in SECRET_PATTERNS.items():
        for match in pattern.finditer(text):
            if "example" in str(rel).lower() and match.group(0).endswith("="):
                continue
            line = text.count("\n", 0, match.start()) + 1
            problems.append(f"{rel}:{line}: {label}")
    for match in ASSISTANT_NAMES.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        problems.append(f"{rel}:{line}: assistant name mentioned ({match.group(0)})")

if problems:
    print("Release check FAILED:")
    for item in problems:
        print("  -", item)
    sys.exit(1)
print("Release check passed: no secrets, env files, or assistant names found.")
