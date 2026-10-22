"""HTTP front for the Blender modeler, meant for Cloud Run (GPU, scale to zero).

POST /model  {"brief": "...", "height": 1.0?}  with `Authorization: Bearer <MODELER_TOKEN>`
  -> 200 application/octet-stream (.glb), headers X-Score, X-Rounds
  -> 4xx/5xx JSON {"error": "..."}
One request = one model; the platform is configured with concurrency 1, so a request owns the GPU.
"""
import hmac
import os
import sys
import tempfile
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nodes"))

from blender_client import BlenderError  # noqa: E402
from modeler import model_asset  # noqa: E402

import subprocess

try:
    print("GPU:", subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=20).stdout.strip() or "none", flush=True)
except Exception as _e:  # noqa: BLE001
    print("GPU: nvidia-smi unavailable", _e, flush=True)

app = Flask(__name__)
TOKEN = os.environ.get("MODELER_TOKEN", "")
_lock = threading.Lock()  # belt and braces: never two Blender runs in one container


@app.get("/healthz")
def healthz():
    return "ok"


@app.post("/model")
def model():
    tok = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not TOKEN or not hmac.compare_digest(tok, TOKEN):
        return jsonify(error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    brief = (data.get("brief") or "").strip()
    if not 20 <= len(brief) <= 1500:
        return jsonify(error="brief must be 20-1500 characters"), 400
    with _lock, tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "asset.glb"
        try:
            r = model_asset(brief, out, Path(tmp) / "previews", height=data.get("height"),
                            log=lambda m: print(m, flush=True), port=9876)
        except BlenderError as e:
            return jsonify(error=str(e)[:500]), 500
        body = out.read_bytes()
    return Response(body, mimetype="application/octet-stream",
                    headers={"X-Score": str(r["score"]), "X-Accuracy": str(r.get("accuracy", r["score"])), "X-Rounds": str(r["rounds"])})
