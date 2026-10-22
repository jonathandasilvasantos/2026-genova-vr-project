# Genova VR server and builder

This folder contains a Flask web application that lists, edits and publishes
Genova VR experiences, and the builder (`builder/`) that generates them from a
text brief. The server runs locally on your machine.

## Contents

```
server/
├── app.py              Flask application (login, experience list, editing, publishing API)
├── requirements.txt    Server dependencies
├── templates/ static/  Web pages
├── experiences/        Published experience bundles served to the player
│   └── solar-system-v3/  Sample experience (ready to use)
└── builder/            Generation pipeline
    ├── generate.py     Command-line entry point
    ├── agents/         Agent definitions (JSON prompts)
    ├── nodes/          Validation, assets, audio, packaging
    ├── schema/         Bundle format (bundle.schema.json)
    └── requirements.txt
```

## 0. Docker (recommended for a quick start)

The image contains Python 3.12, espeak-ng, the server and the builder. It does
not contain Blender, so generation runs with `--no-models` unless you add Blender.

```sh
cd server
cp .env.example .env          # then edit .env: set OPENROUTER_API_KEY, SESSION_SECRET, ADMIN_PASSWORD_HASH
docker build -t genova-vr-server .
docker run --rm -p 8080:8080 --env-file .env -v "$PWD/data:/app/data" genova-vr-server
```

Open `http://127.0.0.1:8080`. To generate an experience inside the container:

```sh
docker run --rm --env-file .env -v "$PWD/data:/app/data" genova-vr-server \
  python builder/generate.py "your brief" --out /app/data/my-experience --no-models
```

Keep `.env` on your machine only. It is excluded from the image by `.dockerignore`.

## 1. Run the server locally (without Docker)

Requirements: Python 3.11 or newer.

```sh
cd server
python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Set the variables below. They are read at start-up.

| Variable | Purpose | Example |
|---|---|---|
| `GENOVA_VR_DATA` | Folder for runtime data (jobs, logs, cache) | `./data` |
| `SESSION_SECRET` | Secret used to sign login sessions. Generate a random value. | output of `secrets.token_hex(32)` |
| `ADMIN_USER` | Login name (default `genova`) | `genova` |
| `ADMIN_PASSWORD_HASH` | Password hash for the login name (see below) | output of `werkzeug.security.generate_password_hash` |
| `COOKIE_SECURE` | Set to `1` only when served over HTTPS | `0` (default) |

Create the password hash:

```sh
python3 -c "from werkzeug.security import generate_password_hash as h; print(h('choose-a-password'))"
```

Start the server:

```sh
export GENOVA_VR_DATA=./data
export SESSION_SECRET="<your random value>"
export ADMIN_PASSWORD_HASH="<hash from the previous step>"
flask --app app run --port 8080
```

Open `http://127.0.0.1:8080` and sign in. The sample experience appears in the
list. The player reads experiences from the same server through
`GET /api/v1/experiences` and `GET /api/v1/experiences/<id>/bundle.zip`.

Do not expose the development server to a network without a proper deployment
(for example gunicorn behind HTTPS). Run it on `127.0.0.1` for local use.

## 2. Generate a new experience (builder)

The builder turns a brief into a bundle in `data/` or the folder you choose.

### 2.1 Install builder dependencies

```sh
pip install -r builder/requirements.txt
```

The narration model also needs `espeak-ng` installed on the system (see 2.2).

### 2.2 Provide API keys

The pipeline calls external services. Export only the keys you need:

| Variable | Used for | Required? |
|---|---|---|
| `OPENROUTER_API_KEY` | Language-model agents (scene design, modelling critique, sound planning) | Yes, for generation |
| `ELEVEN_API_KEY` | Sound effects and ambient audio (ElevenLabs) | Optional (`--no-sfx` disables it) |
| `GEMINI_API_KEY` | Background music (Google Lyria) | Optional (`--no-music` disables it) |
| *(none)* | Narration runs locally with Kokoro-82M, an open-weights model. No key is needed. | Only the first run downloads the model weights |

Keys are read from the environment or from a `.env` file in `server/builder/`.
Never commit `.env` files. `.env` is ignored by `.gitignore`.

> **Narration.** Narration uses [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M),
> an open-weights English text-to-speech model released under the Apache License 2.0.
> It runs locally: the first run downloads the weights, and no key is needed.
> Install the system package `espeak-ng` (for example `brew install espeak-ng` on
> macOS or `sudo apt install espeak-ng` on Debian/Ubuntu), which Kokoro uses for
> words it does not know. Narration is English only; the default voice is
> `af_heart`, and `am_michael` is also available. Pass `--no-tts` to build without narration.

### 2.3 Build an experience

```sh
cd builder
python3 generate.py "A short lesson on how the seasons change, for 12-year-olds" --out ../../data/seasons
```

Useful options:

- `--no-tts` skips narration.
- `--no-sfx` skips sound effects.
- `--no-music` skips background music.
- `--no-models` replaces generated 3D models with plain solids (no Blender needed).
- `--voice af_heart` chooses a Kokoro voice.

Each run calls paid model APIs. Start with `--no-tts --no-sfx --no-music --no-models`
to test the pipeline cheaply, then enable the rest.

### 2.4 3D models (optional)

Generated models are written as Blender Python code and rendered with a headless
Blender instance through `nodes/blender_bridge.py`. Install Blender 4.x and make
its binary reachable from the bridge (see the comments at the top of
`nodes/blender_bridge.py`). Without Blender, use `--no-models`.

## 3. Test the installation

```sh
python3 -m py_compile app.py builder/generate.py
```

Then open the sample experience in the browser and in the Android player.

## Troubleshooting

- **`ModuleNotFoundError`**: activate the virtual environment and reinstall the requirements.
- **Login fails**: regenerate `ADMIN_PASSWORD_HASH` and restart the server.
- **Generation fails with an API error**: check the key for that provider and its billing status.
- **Player shows no experiences**: check that the server is reachable from the phone and that the experience is published.
