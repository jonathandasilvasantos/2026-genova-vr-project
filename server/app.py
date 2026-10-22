"""Genova VR web app: create / edit / publish VR experiences and serve them to the Android app.

Run:  GENOVA_VR_DATA=./data ADMIN_PASSWORD_HASH=... SESSION_SECRET=... flask --app app run
Pages need a login; the Android API (/api/v1/experiences*) only exposes PUBLISHED experiences.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

HERE = Path(__file__).resolve().parent
# Load server/.env (git-ignored) before any configuration is read.
sys.path.insert(0, str(Path(__file__).resolve().parent / "builder" / "nodes"))
import envfile  # noqa: E402
envfile.load(Path(__file__).resolve().parent / ".env")
PROJECT = Path(os.environ.get("GENOVA_VR_PROJECT", HERE / "builder"))  # contains generate.py, nodes/, agents/
DATA = Path(os.environ.get("GENOVA_VR_DATA", HERE / "data"))
PYTHON = os.environ.get("GENOVA_VR_PYTHON", sys.executable)
ADMIN_USER = os.environ.get("ADMIN_USER", "genova")
ADMIN_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")

sys.path.insert(0, str(PROJECT / "nodes"))
import align as align_mod  # noqa: E402
import library as lib_mod  # noqa: E402
import tts as tts_mod  # noqa: E402

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
                  MAX_CONTENT_LENGTH=64 * 1024 * 1024)

for sub in ("experiences", "published", "logs"):
    (DATA / sub).mkdir(parents=True, exist_ok=True)

_db_lock = threading.Lock()


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DATA / "genova-vr.sqlite", timeout=30)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def _close(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    con = sqlite3.connect(DATA / "genova-vr.sqlite")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS experiences (
      id TEXT PRIMARY KEY, title TEXT, brief TEXT, language TEXT DEFAULT 'en',
      created REAL, updated REAL, draft_version INTEGER DEFAULT 0, published_version INTEGER DEFAULT 0,
      summary TEXT DEFAULT '', scenes INTEGER DEFAULT 0, duration_s INTEGER DEFAULT 0, options TEXT DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS jobs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, experience_id TEXT, kind TEXT, instructions TEXT DEFAULT '',
      status TEXT, stage TEXT DEFAULT '', created REAL, started REAL, finished REAL, version INTEGER DEFAULT 0,
      error TEXT DEFAULT '');
    """)
    con.commit()
    con.close()


init_db()


# ---------------------------------------------------------------- auth
def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("user"):
            if request.path.startswith("/api/") or request.headers.get("X-Requested-With"):
                return jsonify(error="auth"), 401
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrapper


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def _csrf():
    if request.method == "POST":
        tok = request.form.get("csrf") or request.headers.get("X-CSRF")
        if not tok or not hmac.compare_digest(tok, session.get("csrf", "")):
            abort(400, "bad csrf token")


_fail = {}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.headers.get("X-Real-IP", request.remote_addr)
        n, t = _fail.get(ip, (0, 0))
        if n >= 5 and time.time() - t < 300:
            flash("Too many attempts. Try again in a few minutes.")
            return render_template("login.html"), 429
        ok = (hmac.compare_digest(request.form.get("user", ""), ADMIN_USER) and ADMIN_HASH
              and check_password_hash(ADMIN_HASH, request.form.get("password", "")))
        if ok:
            _fail.pop(ip, None)
            session.clear()
            session["user"] = ADMIN_USER
            session.permanent = True
            nxt = request.args.get("next", "/")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
        _fail[ip] = (n + 1, time.time())
        flash("Wrong user or password.")
        return render_template("login.html"), 401
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- helpers
def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    return s or "experience"


def exp_dir(eid):
    return DATA / "experiences" / eid


def version_dir(eid, n):
    return exp_dir(eid) / f"v{n}"


def pub_dir(eid):
    return DATA / "published" / eid


def manifest_info(bundle: Path):
    m = json.loads((bundle / "manifest.json").read_text())
    dur = sum(st.get("duration", 0) for sc in m["scenes"] for st in sc["steps"])
    return {"title": m["title"], "summary": m.get("summary", ""), "language": m.get("language", "en"),
            "scenes": len(m["scenes"]), "duration_s": int(dur)}


def dir_size(p: Path):
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def make_zip(src: Path, dest: Path):
    tmp = dest.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:  # media is already compressed
        for f in sorted(src.rglob("*")):
            if f.is_file() and f.name not in ("outline.json",) and "previews" not in f.parts:
                z.write(f, f.relative_to(src).as_posix())
    tmp.replace(dest)
    return hashlib.sha256(dest.read_bytes()).hexdigest()


# ---------------------------------------------------------------- pages
@app.route("/")
@login_required
def index():
    rows = db().execute("SELECT * FROM experiences ORDER BY updated DESC").fetchall()
    running = {r["experience_id"]: r for r in db().execute(
        "SELECT * FROM jobs WHERE status IN ('queued','running','waiting_assets')")}
    return render_template("index.html", exps=rows, running=running)


@app.route("/new", methods=["GET", "POST"])
@login_required
def new():
    if request.method == "POST":
        brief = request.form.get("brief", "").strip()
        if len(brief) < 20:
            flash("Please describe the experience in at least a couple of sentences.")
            return render_template("new.html", brief=brief), 400
        opts = {"music": "music" in request.form, "sfx": "sfx" in request.form, "models": "models" in request.form}
        title = request.form.get("title", "").strip() or brief[:60]
        eid = slug(title)
        base, i = eid, 2
        while db().execute("SELECT 1 FROM experiences WHERE id=?", (eid,)).fetchone():
            eid, i = f"{base}-{i}", i + 1
        now = time.time()
        db().execute("INSERT INTO experiences(id,title,brief,created,updated,options) VALUES(?,?,?,?,?,?)",
                     (eid, title, brief, now, now, json.dumps(opts)))
        job_id = enqueue(eid, "create", "")
        return redirect(url_for("job_page", job_id=job_id))
    return render_template("new.html", brief="")


@app.route("/library")
@login_required
def library_page():
    lib = lib_mod.Library()
    kinds = {k: sorted(lib.list(k).items(), key=lambda kv: -kv[1].get("uses", 0)) for k in ("music", "sfx", "models")}
    return render_template("library.html", kinds=kinds, usage=lib_mod.summary(30), caps=lib_mod.DEFAULT_CAPS,
                           today={p: lib_mod.paid_today(p) for p in lib_mod.DEFAULT_CAPS})


@app.route("/library/<kind>/<path:name>")
@login_required
def library_file(kind, name):
    if kind not in ("music", "sfx", "models") or "/" in name or ".." in name:
        abort(404)
    f = lib_mod.LIB / kind / name
    if not f.exists() or name == "index.json":
        abort(404)
    return send_file(f)


def draft_manifest(eid):
    e = db().execute("SELECT * FROM experiences WHERE id=?", (eid,)).fetchone() or abort(404)
    if not e["draft_version"]:
        abort(404)
    return e, json.loads((version_dir(eid, e["draft_version"]) / "manifest.json").read_text())


def new_version_from_draft(e):
    """Copy the current draft to a new version directory and make it the draft."""
    nxt = e["draft_version"] + 1
    dst = version_dir(e["id"], nxt)
    shutil.copytree(version_dir(e["id"], e["draft_version"]), dst)
    return nxt, dst


def commit_version(eid, n, manifest, dst):
    (dst / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    info = manifest_info(dst)
    db().execute("UPDATE experiences SET draft_version=?, title=?, summary=?, scenes=?, duration_s=?, updated=? WHERE id=?",
                 (n, info["title"], info["summary"], info["scenes"], info["duration_s"], time.time(), eid))
    db().commit()


@app.route("/experience/<eid>/v<int:n>/file/<path:name>")
@login_required
def version_file(eid, n, name):
    root = version_dir(eid, n).resolve()
    f = (root / name).resolve()
    if not str(f).startswith(str(root) + os.sep) or not f.is_file():
        abort(404)
    return send_file(f)


@app.route("/experience/<eid>/asset/<aid>/regenerate", methods=["POST"])
@login_required
def asset_regenerate(eid, aid):
    e, m = draft_manifest(eid)
    if not any(a["id"] == aid for a in m.get("assets", [])):
        abort(404)
    spec = {"asset_id": aid, "brief": request.form.get("brief", "").strip()}
    return redirect(url_for("job_page", job_id=enqueue(eid, "asset", json.dumps(spec))))


def _check_glb(data: bytes):
    import struct
    if len(data) < 28 or data[:4] != b"glTF" or struct.unpack("<I", data[4:8])[0] != 2:
        raise ValueError("not a binary glTF 2.0 (.glb) file")
    if len(data) > 12 * 1024 * 1024:
        raise ValueError("file is larger than 12 MB")
    n = struct.unpack("<I", data[12:16])[0]
    j = json.loads(data[20:20 + n])
    if not j.get("meshes"):
        raise ValueError("the model has no meshes")


@app.route("/experience/<eid>/asset/<aid>/upload", methods=["POST"])
@login_required
def asset_upload(eid, aid):
    e, m = draft_manifest(eid)
    asset = next((a for a in m.get("assets", []) if a["id"] == aid), None) or abort(404)
    f = request.files.get("glb")
    try:
        data = f.read() if f else b""
        _check_glb(data)
    except (ValueError, json.JSONDecodeError, KeyError) as ex:
        flash(f"Upload rejected: {ex}")
        return redirect(url_for("experience", eid=eid))
    n, dst = new_version_from_draft(e)
    (dst / "models").mkdir(exist_ok=True)
    (dst / "models" / f"{aid}.glb").write_bytes(data)
    m = json.loads((dst / "manifest.json").read_text())
    for a in m["assets"]:
        if a["id"] == aid:
            a["file"], a["score"] = f"models/{aid}.glb", 10
    for sc in m["scenes"]:
        for node in sc["nodes"]:
            if node.get("asset") == aid:
                node["kind"], node["model"] = "model", f"models/{aid}.glb"
    commit_version(eid, n, m, dst)
    flash(f"Model '{aid}' replaced in version {n}.")
    return redirect(url_for("experience", eid=eid))


@app.route("/experience/<eid>/narration", methods=["POST"])
@login_required
def edit_narration(eid):
    """Edit narration text per step; changed steps are re-spoken (Kokoro TTS) and cues re-aligned."""
    e, m = draft_manifest(eid)
    changes = []
    for si, sc in enumerate(m["scenes"]):
        for k, st in enumerate(sc["steps"]):
            new = (request.form.get(f"t_{si}_{k}") or "").strip()
            if new and new != st["text"]:
                if len(new) > 400:
                    flash(f"Scene {si + 1} step {k + 1} is too long (max 400 characters).")
                    return redirect(url_for("experience", eid=eid))
                changes.append((si, k, new))
    if not changes:
        flash("No changes.")
        return redirect(url_for("experience", eid=eid))
    n, dst = new_version_from_draft(e)
    m = json.loads((dst / "manifest.json").read_text())
    tts = tts_mod.TTSClient(cache_dir=Path(os.environ.get("GENOVA_VR_CACHE", DATA / "cache")) / "tts")
    try:
        for si, k, new in changes:
            st = m["scenes"][si]["steps"][k]
            wav = tts.speak(new, language=m["language"])
            rel = st.get("audio") or f"audio/{m['scenes'][si]['id']}_{k:02d}.wav"
            (dst / rel).write_bytes(wav)
            st["text"], st["audio"], st["duration"] = new, rel, round(tts_mod.wav_duration(wav), 2)
            if st.get("cues"):
                words = align_mod.word_times(wav, new)
                kept = []
                for cue in st["cues"]:
                    at = align_mod.phrase_time(words, cue.get("phrase", ""))
                    if at is not None:
                        cue["at"] = round(min(at, max(0.0, st["duration"] - 0.3)), 2)
                        kept.append(cue)
                st["cues"] = kept
                if not kept:
                    st.pop("cues")
    except tts_mod.TTSError as ex:
        shutil.rmtree(dst, ignore_errors=True)
        flash(f"Could not re-record the narration: {ex}")
        return redirect(url_for("experience", eid=eid))
    commit_version(eid, n, m, dst)
    flash(f"Narration updated ({len(changes)} step{'s' if len(changes) != 1 else ''}) in version {n}.")
    return redirect(url_for("experience", eid=eid))


@app.route("/import", methods=["GET", "POST"])
@login_required
def import_bundle():
    """Create an experience from an existing bundle .zip (for example one built on a worker machine)."""
    if request.method == "POST":
        f = request.files.get("bundle")
        if not f or not f.filename.lower().endswith(".zip"):
            flash("Choose a .zip bundle.")
            return render_template("import.html"), 400
        tmp = DATA / "logs" / f"import-{secrets.token_hex(6)}"
        tmp.mkdir(parents=True)
        zpath = tmp / "in.zip"
        f.save(zpath)
        root = tmp / "x"
        try:
            with zipfile.ZipFile(zpath) as z:
                for name in z.namelist():
                    target = (root / name).resolve()
                    if not str(target).startswith(str(root.resolve()) + os.sep):
                        raise ValueError("unsafe path in zip")
                z.extractall(root)
            if not (root / "manifest.json").exists():
                raise ValueError("manifest.json not found at the top level of the zip")
            info = manifest_info(root)
        except (ValueError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as e:
            shutil.rmtree(tmp, ignore_errors=True)
            flash(f"Not a valid bundle: {e}")
            return render_template("import.html"), 400
        title = request.form.get("title", "").strip() or info["title"]
        eid = slug(title)
        base, i = eid, 2
        while db().execute("SELECT 1 FROM experiences WHERE id=?", (eid,)).fetchone():
            eid, i = f"{base}-{i}", i + 1
        dest = version_dir(eid, 1)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root), str(dest))
        shutil.rmtree(tmp, ignore_errors=True)
        now = time.time()
        db().execute("INSERT INTO experiences(id,title,brief,created,updated,draft_version,summary,scenes,duration_s,language) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (eid, title, "(imported bundle)", now, now, 1, info["summary"], info["scenes"], info["duration_s"], info["language"]))
        db().commit()
        flash("Imported. Review it, then publish.")
        return redirect(url_for("experience", eid=eid))
    return render_template("import.html")


def enqueue(eid, kind, instructions):
    cur = db().execute("INSERT INTO jobs(experience_id,kind,instructions,status,created) VALUES(?,?,?,?,?)",
                       (eid, kind, instructions, "queued", time.time()))
    db().commit()
    return cur.lastrowid


@app.route("/experience/<eid>")
@login_required
def experience(eid):
    e = db().execute("SELECT * FROM experiences WHERE id=?", (eid,)).fetchone() or abort(404)
    jobs = db().execute("SELECT * FROM jobs WHERE experience_id=? ORDER BY id DESC LIMIT 10", (eid,)).fetchall()
    outline = None
    if e["draft_version"]:
        f = version_dir(eid, e["draft_version"]) / "outline.json"
        if f.exists():
            outline = json.loads(f.read_text())
    manifest = None
    if e["draft_version"]:
        try:
            manifest = json.loads((version_dir(eid, e["draft_version"]) / "manifest.json").read_text())
        except (OSError, json.JSONDecodeError):
            manifest = None
    return render_template("experience.html", e=e, jobs=jobs, outline=outline, manifest=manifest,
                           opts=json.loads(e["options"] or "{}"),
                           versions=sorted(int(p.name[1:]) for p in exp_dir(eid).glob("v*") if p.is_dir()),
                           busy=any(j["status"] in ("queued", "running", "waiting_assets") for j in jobs))


@app.route("/experience/<eid>/edit", methods=["POST"])
@login_required
def edit(eid):
    e = db().execute("SELECT * FROM experiences WHERE id=?", (eid,)).fetchone() or abort(404)
    instr = request.form.get("instructions", "").strip()
    if len(instr) < 5:
        flash("Describe what to change.")
        return redirect(url_for("experience", eid=eid))
    if not e["draft_version"]:
        flash("Wait for the first version to finish.")
        return redirect(url_for("experience", eid=eid))
    return redirect(url_for("job_page", job_id=enqueue(eid, "edit", instr)))


@app.route("/experience/<eid>/models", methods=["POST"])
@login_required
def complete_models(eid):
    db().execute("SELECT 1 FROM experiences WHERE id=?", (eid,)).fetchone() or abort(404)
    return redirect(url_for("job_page", job_id=enqueue(eid, "models", "")))


@app.route("/experience/<eid>/publish", methods=["POST"])
@login_required
def publish(eid):
    e = db().execute("SELECT * FROM experiences WHERE id=?", (eid,)).fetchone() or abort(404)
    n = int(request.form.get("version") or e["draft_version"])
    src = version_dir(eid, n)
    if not (src / "manifest.json").exists():
        abort(404)
    pv = e["published_version"] + 1 if e["published_version"] else 1
    dest = pub_dir(eid)
    dest.mkdir(parents=True, exist_ok=True)
    zip_path = dest / "bundle.zip"
    sha = make_zip(src, zip_path)
    info = manifest_info(src)
    meta = {"id": eid, "version": pv, "source_version": n, "sha256": sha, "size": zip_path.stat().st_size,
            "published_at": time.time(), **info}
    (dest / "meta.json").write_text(json.dumps(meta))
    db().execute("UPDATE experiences SET published_version=?, updated=? WHERE id=?", (pv, time.time(), eid))
    db().commit()
    flash(f"Published version {pv}. It is now available in the Genova VR app.")
    return redirect(url_for("experience", eid=eid))


@app.route("/experience/<eid>/unpublish", methods=["POST"])
@login_required
def unpublish(eid):
    shutil.rmtree(pub_dir(eid), ignore_errors=True)
    db().execute("UPDATE experiences SET published_version=0 WHERE id=?", (eid,))
    db().commit()
    flash("Removed from the app.")
    return redirect(url_for("experience", eid=eid))


@app.route("/experience/<eid>/delete", methods=["POST"])
@login_required
def delete(eid):
    shutil.rmtree(pub_dir(eid), ignore_errors=True)
    shutil.rmtree(exp_dir(eid), ignore_errors=True)
    db().execute("DELETE FROM experiences WHERE id=?", (eid,))
    db().execute("DELETE FROM jobs WHERE experience_id=?", (eid,))
    db().commit()
    return redirect(url_for("index"))


@app.route("/experience/<eid>/v<int:n>/download")
@login_required
def download_version(eid, n):
    src = version_dir(eid, n)
    if not src.exists():
        abort(404)
    tmp = DATA / "logs" / f"{eid}-v{n}.zip"
    make_zip(src, tmp)
    return send_file(tmp, as_attachment=True, download_name=f"{eid}-v{n}.zip")


@app.route("/job/<int:job_id>")
@login_required
def job_page(job_id):
    j = db().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone() or abort(404)
    return render_template("job.html", j=j)


@app.route("/api/job/<int:job_id>")
@login_required
def job_api(job_id):
    j = db().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone() or abort(404)
    log = DATA / "logs" / f"job{job_id}.log"
    text = log.read_text(errors="replace")[-12000:] if log.exists() else ""
    return jsonify(status=j["status"], stage=j["stage"], error=j["error"], version=j["version"],
                   experience_id=j["experience_id"], log=text)


@app.route("/api/job/<int:job_id>/cancel", methods=["POST"])
@login_required
def job_cancel(job_id):
    with _db_lock:
        pr = RUNNING.get(job_id)
    if pr:
        pr.terminate()
    db().execute("UPDATE jobs SET status='cancelled', finished=? WHERE id=? AND status IN ('queued','running','waiting_assets')",
                 (time.time(), job_id))
    db().commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- public API for the Android app
def published_list():
    out = []
    for meta in sorted((DATA / "published").glob("*/meta.json")):
        try:
            out.append(json.loads(meta.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    out.sort(key=lambda m: -m["published_at"])
    return out


@app.route("/api/v1/experiences")
def api_list():
    base = request.url_root.rstrip("/")
    if request.headers.get("X-Forwarded-Proto") == "https":
        base = base.replace("http://", "https://")
    items = []
    for m in published_list():
        items.append({"id": m["id"], "title": m["title"], "summary": m["summary"], "language": m["language"],
                      "version": m["version"], "scenes": m["scenes"], "duration_s": m["duration_s"],
                      "size": m["size"], "sha256": m["sha256"], "updated": int(m["published_at"]),
                      "url": f"{base}/api/v1/experiences/{m['id']}/bundle.zip"})
    return jsonify(experiences=items)


@app.route("/api/v1/experiences/<eid>/bundle.zip")
def api_bundle(eid):
    if not re.fullmatch(r"[a-z0-9-]+", eid):
        abort(404)
    f = pub_dir(eid) / "bundle.zip"
    if not f.exists():
        abort(404)
    return send_file(f, mimetype="application/zip", conditional=True)


# ---------------------------------------------------------------- job runner (one at a time)
RUNNING = {}
_runner_started = False


def _set(job_id, **kw):
    con = sqlite3.connect(DATA / "genova-vr.sqlite", timeout=30)
    con.execute(f"UPDATE jobs SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), job_id))
    con.commit()
    con.close()


STAGE_RE = re.compile(r"^(\d)/4 (\w+)|^\s+(modelling|asking|reusing|soundtrack|sound design|narrated|3D models)", re.I)


def run_job(job):
    con = sqlite3.connect(DATA / "genova-vr.sqlite", timeout=30)
    con.row_factory = sqlite3.Row
    e = con.execute("SELECT * FROM experiences WHERE id=?", (job["experience_id"],)).fetchone()
    con.close()
    if not e:
        _set(job["id"], status="failed", error="experience not found", finished=time.time())
        return
    eid = e["id"]
    opts = json.loads(e["options"] or "{}")
    nxt = (e["draft_version"] or 0) + 1
    out = version_dir(eid, nxt)
    cmd = [PYTHON, "-u", str(PROJECT / "generate.py"), e["brief"], "--out", str(out)]
    if e["draft_version"] and job["kind"] in ("edit", "models", "asset"):
        cmd += ["--base", str(version_dir(eid, e["draft_version"]))]
    if job["kind"] == "asset":
        spec = json.loads(job["instructions"] or "{}")
        cmd += ["--regen-asset", spec["asset_id"]]
        if spec.get("brief"):
            cmd += ["--asset-brief", spec["brief"]]
    if job["kind"] == "edit":
        cmd += ["--revise", job["instructions"]]
    if not opts.get("music", True):
        cmd.append("--no-music")
    if not opts.get("sfx", True):
        cmd.append("--no-sfx")
    if not opts.get("models", True):
        cmd.append("--no-models")
    env = dict(os.environ, GENOVA_VR_JOB=str(job["id"]), PYTHONUNBUFFERED="1")
    log = DATA / "logs" / f"job{job['id']}.log"
    _set(job["id"], status="running", started=time.time(), stage="starting")
    with open(log, "w") as lf:
        pr = subprocess.Popen(cmd, cwd=PROJECT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        with _db_lock:
            RUNNING[job["id"]] = pr
        for line in pr.stdout:
            lf.write(line)
            lf.flush()
            m = STAGE_RE.match(line)
            if m:
                _set(job["id"], stage=line.strip()[:80],
)
        code = pr.wait()
    with _db_lock:
        RUNNING.pop(job["id"], None)
    cur = sqlite3.connect(DATA / "genova-vr.sqlite", timeout=30)
    status = cur.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
    cur.close()
    if status == "cancelled":
        shutil.rmtree(out, ignore_errors=True)
        return
    if code == 0 and (out / "manifest.json").exists():
        info = manifest_info(out)
        con = sqlite3.connect(DATA / "genova-vr.sqlite", timeout=30)
        con.execute("UPDATE experiences SET draft_version=?, title=?, summary=?, language=?, scenes=?, duration_s=?, updated=? WHERE id=?",
                    (nxt, info["title"], info["summary"], info["language"], info["scenes"], info["duration_s"], time.time(), eid))
        con.commit()
        con.close()
        _set(job["id"], status="done", finished=time.time(), version=nxt, stage="done")
    else:
        tail = log.read_text(errors="replace")[-600:]
        shutil.rmtree(out, ignore_errors=True)
        _set(job["id"], status="failed", finished=time.time(), error=tail)


def runner_loop():
    while True:
        con = sqlite3.connect(DATA / "genova-vr.sqlite", timeout=30)
        con.row_factory = sqlite3.Row
        job = con.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        con.close()
        if job:
            try:
                run_job(job)
            except Exception as ex:  # noqa: BLE001 - never let the runner die
                _set(job["id"], status="failed", error=str(ex)[:500], finished=time.time())
        else:
            time.sleep(2)


def start_runner():
    global _runner_started
    if _runner_started:
        return
    _runner_started = True
    # jobs left 'running' by a previous process are failed so the queue does not stall
    con = sqlite3.connect(DATA / "genova-vr.sqlite", timeout=30)
    con.execute("UPDATE jobs SET status='failed', error='server restarted', finished=? WHERE status IN ('running','waiting_assets')",
                (time.time(),))
    con.commit()
    con.close()
    threading.Thread(target=runner_loop, daemon=True).start()


@app.route("/healthz")
def healthz():
    return "ok"


if os.environ.get("GENOVA_VR_RUNNER", "1") == "1":
    start_runner()
