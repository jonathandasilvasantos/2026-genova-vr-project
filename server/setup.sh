#!/bin/sh
# Set up the Genova VR server and builder on macOS or Linux.
# Usage: sh setup.sh            (run from the server/ folder)
set -eu
cd "$(dirname "$0")"

say() { printf '\n==> %s\n' "$1"; }

# 1. Python 3.10-3.12 is required by the narration package (Kokoro).
PY=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version=$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
        case "$version" in
            3.10|3.11|3.12) PY="$candidate"; break ;;
        esac
    fi
done
if [ -z "$PY" ]; then
    echo "Python 3.10, 3.11 or 3.12 is required (found: $(python3 --version 2>/dev/null || echo none))." >&2
    echo "Install one, for example: brew install python@3.12  or  sudo apt install python3.12-venv" >&2
    exit 1
fi
say "Using $PY ($("$PY" --version))"

# 2. Virtual environment and dependencies.
say "Creating .venv"
[ -d .venv ] || "$PY" -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
say "Installing server requirements"
pip install -r requirements.txt
say "Installing builder requirements"
pip install -r builder/requirements.txt

# 3. espeak-ng (used by Kokoro for pronunciation).
say "Checking espeak-ng"
if ! command -v espeak-ng >/dev/null 2>&1; then
    echo "espeak-ng is not installed. Install it, then run this script again:"
    echo "  macOS:         brew install espeak-ng"
    echo "  Debian/Ubuntu: sudo apt install espeak-ng"
    exit 1
fi

# 3b. Point Kokoro at espeak-ng's data folder if the variable is not already set.
espeak_dir=""
for candidate in /opt/homebrew/share/espeak-ng-data /usr/local/share/espeak-ng-data /usr/share/espeak-ng-data; do
    if [ -d "$candidate" ]; then espeak_dir="$candidate"; break; fi
done

# 4. Configuration file (never overwritten).
say "Preparing server/.env"
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo "Created .env from .env.example. Edit it and set your values."
else
    echo ".env already exists; left unchanged."
fi
if [ -n "$espeak_dir" ] && ! grep -q '^ESPEAK_DATA_PATH=.\+' .env; then
    # Replace the empty line (or add the key) with the detected path.
    tmp=$(mktemp)
    grep -v '^ESPEAK_DATA_PATH=' .env > "$tmp" || true
    printf 'ESPEAK_DATA_PATH=%s\n' "$espeak_dir" >> "$tmp"
    mv "$tmp" .env
    chmod 600 .env
    echo "Set ESPEAK_DATA_PATH=$espeak_dir in .env"
fi
mkdir -p data

say "Setup finished"
cat <<'MSG'
Next steps:
  1. Edit server/.env: set OPENROUTER_API_KEY (to generate experiences),
     SESSION_SECRET and ADMIN_PASSWORD_HASH (to log in to the web app).
     Set GENOVA_LLM_MODEL to the model you want to use.
  2. Start the web app:   . .venv/bin/activate && flask --app app run --port 8080
  3. Generate an experience (optional):
       cd builder && python generate.py "your brief" --out ../data/my-experience
MSG
