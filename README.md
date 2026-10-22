# Genova VR

Genova VR turns a natural-language teaching brief into a guided virtual reality
experience: 3D scenes, narrated steps, sound, and a closing quiz. An experience
is produced as a self-contained *bundle* (a JSON manifest plus assets) that a
generic Android player displays in a Google Cardboard viewer. A new experience
is therefore data, not new application code.

This repository contains the release components:

| Folder | What it is | Start here |
|---|---|---|
| `server/` | Local web server for experiences, plus the builder that generates them | [`server/README.md`](server/README.md) |
| `client-vr/` | Android Cardboard player: source code and a debug APK | [`client-vr/README.md`](client-vr/README.md) |
| `tools/` | Helper scripts (fetches pinned third-party SDK sources) | see `client-vr/README.md` |

## Quick start

You need Python 3.11 or newer. The Android steps need JDK 17 and the Android SDK
only if you build the app yourself; installing the provided APK needs only `adb`
or a file transfer to the phone.

1. **Serve the sample experience locally.**
   ```sh
   cd server
   python3 -m venv .venv && . .venv/bin/activate
   pip install -r requirements.txt
   export GENOVA_VR_DATA=./data
   export SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
   flask --app app run --port 8080
   ```
   Open `http://127.0.0.1:8080`. The sample experience in `server/experiences/`
   is listed there. Login is required; see `server/README.md` to set the admin
   password.

2. **Install the player.** Copy `client-vr/apk/genova-vr-debug.apk` to an Android
   phone, allow installation from that source, and open *Genova VR*. To point the
   player at your local server, follow the instructions in
   `client-vr/README.md` (the default server URL is set at build time).

3. **Generate a new experience (optional, needs API keys).** See
   `server/README.md#generating-an-experience`. Generation calls external model
   services and is billed by those providers.

## What is in this release

- **Pipeline and builder**: `server/builder/` (`generate.py`, `agents/`,
  `nodes/`, `schema/`). Agents are JSON prompt definitions; `schema/` defines the
  bundle format; `nodes/` contains validation, asset, audio and packaging code.
- **Server**: `server/app.py` (Flask) with its templates and static files.
- **Player**: `client-vr/app/` (Java and C++ on the Google Cardboard SDK).
- **Sample experience**: `server/experiences/solar-system-v3/`.
- **Debug APK**: `client-vr/apk/genova-vr-debug.apk`, built from the included source.

## Scope and limitations

- This release is an engineering snapshot of the source code and documentation. It is not a
  production service, and it contains no credentials, deployment scripts, or
  private research records.
- The generation pipeline depends on external model services. Their outputs vary
  between runs, so an identical brief may produce a different experience.
- The player has been exercised on opening scenes and on a controlled quiz check;
  it has not been validated for full playback of every experience, comfort, or
  accessibility. Evidence and its limits are described in the sections above.
- Generated content is not checked for factual accuracy by subject-matter experts.
  Review each experience before using it with learners.

## Licence

MIT. See [`LICENSE`](LICENSE). Third-party components keep their own licenses.
