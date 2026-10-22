#!/usr/bin/env python3
"""Teacher prompt -> VR experience bundle.

    .venv/bin/python generate.py "teacher brief ..." --out bundles/my-experience

Stages (each is a Genova-style agent or node, see agents/ and nodes/):
  1 director        brief -> outline (scenes)
  2 scene designer  outline -> scene JSON per scene (validated, auto-retried)
  2b 3D models      Blender modeller + visual critic -> models/*.glb (assets chosen by the director, up to 50; existing files are reused)
  3 sky textures    procedural
  4 narrator TTS    Kokoro-82M -> audio/*.wav, durations
  5 sound designer  sparing ambience bed / effects, rendered by ElevenLabs -> audio/*.mp3
  6 composer        1-3 music tracks for the whole experience (Gemini Lyria), crossfaded by the player
  7 validate        schema + semantic checks
Nothing here is subject-specific.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE_ROOT = Path(os.environ.get("GENOVA_VR_CACHE", ROOT / ".cache"))
sys.path.insert(0, str(ROOT / "nodes"))

from elevenlabs import SfxError, generate as make_sfx  # noqa: E402
from llm import Agent, LLMError  # noqa: E402
from modeler import model_asset  # noqa: E402
from blender_client import BlenderError  # noqa: E402
from lyria import MusicError, generate as make_music  # noqa: E402
from musicfx import process as process_music  # noqa: E402
from library import Library, norm, record  # noqa: E402
import align  # noqa: E402
import effects  # noqa: E402
import layout_check  # noqa: E402
from music_moods import MOODS, catalog as music_catalog, prompt_for  # noqa: E402
from skygen import starfield  # noqa: E402
from tts import TTSClient, TTSError, wav_duration  # noqa: E402
import envfile  # noqa: E402
envfile.load(Path(__file__).resolve().parent.parent / ".env")
envfile.load(Path(__file__).resolve().parent / ".env")
from validate import validate  # noqa: E402
from experiment_trace import emit
from cue_timing import discard_unresolved_cues
from api_budget import active_budget, BudgetError


def log(msg):
    print(msg, flush=True)


def plan(brief, director, previous=None, revision=None, scene_count=None, required_assets=()):
    def check(o):
        errs = []
        if not isinstance(o, dict):
            return ["plan must be a JSON object"]
        if not isinstance(o.get("scenes"), list) or not 1 <= len(o["scenes"]) <= 8:
            errs.append("scenes must be a list of 1..8 items")
            return errs
        ids = [s.get("id") for s in o["scenes"]]
        if len(set(ids)) != len(ids):
            errs.append("scene ids must be unique")
        for s in o["scenes"]:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", str(s.get("id", ""))):
                errs.append(f"bad scene id {s.get('id')!r}")
        if scene_count is not None and len(o["scenes"]) != scene_count:
            errs.append(f"the brief requires exactly {scene_count} scenes, including the final quiz scene")
        if required_assets:
            assets = o.get("assets", [])
            if not isinstance(assets, list):
                errs.append("assets must be a list")
            else:
                declared = {asset.get("id") for asset in assets if isinstance(asset, dict)}
                for identifier in required_assets:
                    if identifier not in declared:
                        errs.append(f"declare the required Blender asset with exact id {identifier}")
        return errs

    q = "TEACHER BRIEF:\n" + brief
    lib_assets = Library().catalog("models", limit=80)
    if lib_assets:
        q += "\n\nLIBRARY_ASSETS (already built, reuse by id when they fit):\n" + json.dumps(lib_assets, ensure_ascii=False)
    if previous and revision:
        q += ("\n\nPREVIOUS PLAN (JSON):\n" + json.dumps(previous, ensure_ascii=False)
              + "\n\nREVISION REQUEST:\n" + revision)
    return director.ask_json(q, validate=check)


def design_scene(brief, outline, index, designer):
    scene = outline["scenes"][index]
    final = index == len(outline["scenes"]) - 1
    context = {"title": outline["title"], "language": outline["language"], "audience": outline.get("audience"),
               "assets": [{"id": a["id"], "brief": a["brief"]} for a in outline.get("assets", [])],
               "all_scenes": [{"id": s["id"], "title": s["title"]} for s in outline["scenes"]]}
    q = (f"TEACHER BRIEF:\n{brief}\n\nEXPERIENCE CONTEXT:\n{json.dumps(context, ensure_ascii=False)}\n\n"
         f"DESIGN THIS SCENE (index {index + 1} of {len(outline['scenes'])}):\n{json.dumps(scene, ensure_ascii=False)}\n\n"
         + ("This IS the final scene: include the quiz.\n" if final else "This is NOT the final scene: do not include a quiz.\n")
         + f"Write all text in language: {outline['language']}.")

    def check(o):
        if not isinstance(o, dict):
            return ["reply must be a JSON object"]
        o["id"] = scene["id"]
        manifest = {"schema": "genova.vr/1", "title": outline["title"], "language": outline["language"],
                    "assets": [{"id": a["id"], "brief": a["brief"]} for a in outline.get("assets", [])], "scenes": [o]}
        errs = validate(manifest)
        if final and "quiz" not in o:
            errs.append("final scene must include a quiz")
        if not final and "quiz" in o:
            errs.append("only the final scene may have a quiz")
        return errs

    log(f"  designing scene {index + 1}: {scene['title']}")
    return designer.ask_json(q, validate=check)


def build_assets(manifest, out, workers=None, skip=False, rebuild=False, visual_repair=True):
    workers = workers or int(os.environ.get("MODELER_PARALLEL", "3"))
    lib = Library()
    """Stage: Blender modeller builds every asset that is actually used (render + critique loop)."""
    used = {n["asset"] for sc in manifest["scenes"] for n in sc["nodes"] if n.get("asset")}
    todo = [a for a in manifest.get("assets", []) if a["id"] in used]
    manifest["assets"] = todo
    (out / "models").mkdir(exist_ok=True)

    def run(item):
        i, a = item
        existing = out / "models" / f"{a['id']}.glb"
        if existing.exists() and not rebuild:
            emit("asset.reused", asset=a["id"], source="bundle")
            a["file"] = f"models/{a['id']}.glb"
            log(f"  reusing {a['id']}")
            return
        hit = None if rebuild else lib.get("models", a["id"])
        if hit:
            emit("asset.reused", asset=a["id"], source="library")
            shutil.copyfile(hit[0], existing)
            lib.touch("models", a["id"])
            record("cloudrun_gpu", "model", cached=True, detail=f"library:{a['id']}")
            a["file"] = f"models/{a['id']}.glb"
            a["score"] = hit[1].get("score")
            log(f"  reusing {a['id']} from the library (no modelling cost)")
            return
        log(f"  modelling {a['id']}")
        try:
            r = model_asset(a["brief"], out / "models" / f"{a['id']}.glb", CACHE_ROOT / "previews" / out.name,
                            log=log, port=9876 + i, visual_repair=visual_repair)
            a["file"] = f"models/{a['id']}.glb"
            if r["score"] is not None:
                a["score"] = r["score"]
            else:
                a.pop("score", None)
            if r["score"] is not None and r["score"] >= 7:  # only good models enter the shared library
                lib.put("models", a["id"], existing.read_bytes(), "glb", brief=a["brief"], score=r["score"],
                        accuracy=r.get("accuracy"))
        except BlenderError as e:
            emit("asset.fallback", asset=a["id"], reason=type(e).__name__)
            log(f"  asset {a['id']} failed ({e}); using a plain sphere instead")

    if not skip:
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(run, enumerate(todo)))
    files = {a["id"]: a.get("file") for a in todo}
    for sc in manifest["scenes"]:
        for n in sc["nodes"]:
            if n.get("asset"):
                f = files.get(n["asset"])
                if f:
                    n["kind"] = "model"
                    n["model"] = f
                    n.pop("color", None) if n.get("color") == [0.6, 0.65, 0.75] else None
                else:  # graceful fallback so the experience still plays; keeps `asset` so it can be upgraded later
                    n["kind"] = "sphere"
                    n.setdefault("color", [0.6, 0.65, 0.75])
                    n.pop("model", None)


def review_layout(manifest, outline, designer, rounds=2):
    """Stage: replay each scene's camera and text on a virtual screen, fix framing automatically, and ask the
    designer to repair what remains (overlapping panels, captions covering the subject, cut-off text)."""
    final_id = manifest["scenes"][-1]["id"]

    def one(index):
        sc = manifest["scenes"][index]
        edited_steps = layout_check.reframe(sc)
        emit("layout.reframed", scene=sc["id"], edited_camera_steps=edited_steps)
        problems = layout_check.review_scene(sc)
        best, best_p = json.loads(json.dumps(sc)), problems
        for r in range(rounds):
            if not best_p:
                break
            emit("layout.revision_attempt", scene=best["id"], attempt=r + 1, flags_before=len(best_p))
            q = ("You designed this scene for a VR experience. A layout checker replayed it on a virtual screen and found "
                 "problems the viewer would see. Fix them by editing the scene, keeping its meaning and content.\n\n"
                 "PROBLEMS:\n- " + "\n- ".join(best_p[:14]) + "\n\nRULES FOR THE FIX: captions (step.text) must be at most 170 characters; "
                 "the highlighted/focused object must be visible and not covered; auto-labels appear only on the highlighted node, so "
                 "use `label` on the nodes the narrator names and set `highlight` on the node being narrated in every step; use at most "
                 "3 text panels (quad with label), keep them small, far from each other, in the upper part of the view (above the caption "
                 "area) and delete the ones that repeat the narration; spread objects out instead of stacking them; do not change ids "
                 "that other nodes or steps refer to. Keep every other field. Return the FULL corrected scene JSON.\n\n"
                 "SCENE:\n" + json.dumps(best, ensure_ascii=False))
            try:
                fixed = designer.ask_json(q, validate=lambda o: _repair_check(o, outline, manifest, best["id"], best["id"] == final_id))
            except LLMError as e:
                log(f"    layout repair skipped for {best['id']}: {e}")
                break
            layout_check.reframe(fixed)
            p2 = layout_check.review_scene(fixed)
            if len(p2) < len(best_p):
                emit("layout.revision_accepted", scene=best["id"], flags_before=len(best_p), flags_after=len(p2))
                best, best_p = fixed, p2
            else:
                emit("layout.revision_rejected", scene=best["id"], flags_before=len(best_p), flags_after=len(p2))
                break
        manifest["scenes"][index] = best
        emit("layout.completed", scene=best["id"], flags_before=len(problems), flags_after=len(best_p))
        log(f"  layout {best['id']}: {len(problems)} problems found, {len(best_p)} left")
        return best_p

    with ThreadPoolExecutor(3) as ex:
        left = list(ex.map(one, range(len(manifest["scenes"]))))
    log(f"  layout review: {sum(len(x) for x in left)} minor problems left")


def _repair_check(o, outline, manifest, scene_id, final):
    if not isinstance(o, dict):
        return ["reply must be a JSON object"]
    o["id"] = scene_id
    m = {"schema": "genova.vr/1", "title": outline["title"], "language": outline["language"],
         "assets": [{"id": a["id"], "brief": a["brief"]} for a in manifest.get("assets", [])], "scenes": [o]}
    errs = validate(m)
    if final and "quiz" not in o:
        errs.append("final scene must keep its quiz")
    if not final and "quiz" in o:
        errs.append("only the final scene may have a quiz")
    return errs


def add_sky(manifest, out):
    made = None
    for sc in manifest["scenes"]:
        sky = sc.get("sky") or {}
        if sky.get("style") == "stars":
            if made is None:
                (out / "textures").mkdir(exist_ok=True)
                starfield(out / "textures" / "stars.png")
                made = "textures/stars.png"
            sc["sky"] = {"texture": made}
        elif sky.get("style") == "plain":
            sc["sky"] = {"color": sky.get("color", [0.05, 0.05, 0.1])}


def narrate(manifest, out, voice, workers=4):
    lang = manifest["language"]
    tts = TTSClient(cache_dir=CACHE_ROOT / "tts")
    (out / "audio").mkdir(exist_ok=True)
    jobs = []
    for sc in manifest["scenes"]:
        for k, st in enumerate(sc["steps"]):
            jobs.append((st, st["text"], f"audio/{sc['id']}_{k:02d}.wav"))
        q = sc.get("quiz")
        if q:
            jobs.append((q, q["question"], f"audio/{sc['id']}_quiz.wav"))
            for name in ("correct", "wrong"):
                if q.get(name):
                    jobs.append((q[name], q[name]["text"], f"audio/{sc['id']}_{name}.wav"))

    def run(job):
        target, text, rel = job
        wav = tts.speak(text, language=lang, voice=voice)
        (out / rel).write_bytes(wav)
        target["audio"] = rel
        target["duration"] = round(wav_duration(wav), 2)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(run, jobs))
    log(f"  narrated {len(jobs)} clips")
    n_cues = 0
    for sc in manifest["scenes"]:
        for st in sc["steps"]:
            cues = st.get("cues") or []
            if not cues or not st.get("audio"):
                st.pop("cues", None)
                continue
            try:
                words = align.word_times((out / st["audio"]).read_bytes(), st["text"])
            except Exception as e:  # noqa: BLE001 - cues are an enhancement, never fail the build
                log(f"    cue alignment skipped ({e})")
                st.pop("cues", None)
                continue
            kept = []
            for cue in cues:
                at = cue.get("at") if cue.get("at") is not None else align.phrase_time(words, cue.get("phrase", ""))
                if at is None:
                    continue
                cue["at"] = round(min(float(at), max(0.0, st.get("duration", at) - 0.3)), 2)
                kept.append(cue)
            kept.sort(key=lambda c: c["at"])
            if kept:
                st["cues"] = kept
                n_cues += len(kept)
            else:
                st.pop("cues", None)
    log(f"  {n_cues} narration cues aligned")


def design_music(manifest, outline, out, agent):
    """Stage: composer picks 1-3 tracks (library moods first); Lyria is called only for moods not yet in the library."""
    ids = [s["id"] for s in manifest["scenes"]]
    lib = Library()
    custom_lib = [c for c in lib.catalog("music") if c["id"].startswith("custom_")]
    allowed = set(MOODS) | {c["id"] for c in custom_lib} | {"custom"}

    def check(o):
        errs = []
        tracks = o.get("tracks") or []
        if not 1 <= len(tracks) <= 3:
            errs.append("tracks must contain 1 to 3 items (hard limit 3)")
        tid = [t.get("id") for t in tracks]
        if len(set(tid)) != len(tid):
            errs.append("track ids must be unique")
        for t in tracks:
            if t.get("mood") not in allowed:
                errs.append(f"track {t.get('id')}: mood must be one of {sorted(allowed)}")
            if t.get("mood") == "custom" and not t.get("prompt"):
                errs.append(f"track {t.get('id')}: custom mood needs a prompt")
        if sum(1 for t in tracks if t.get("mood") == "custom") > 1:
            errs.append("at most one custom track per experience")
        scenes = {s.get("id"): s.get("music") for s in o.get("scenes") or []}
        if set(scenes) != set(ids):
            errs.append(f"scenes must list exactly these ids: {ids}")
        else:
            if scenes[ids[0]] in (None, "none"):
                errs.append("the first scene must name a track")
            used = {m for m in scenes.values() if m and m != "none"}
            if not used <= set(tid):
                errs.append("a scene refers to an unknown track")
            if used != set(tid):
                errs.append("every track must be used by at least one scene")
            if sum(1 for m in scenes.values() if m == "none") > 1:
                errs.append("at most one scene may be silent")
        return errs

    brief = {"title": outline["title"], "audience": outline.get("audience"), "summary": outline.get("summary"),
             "scenes": [{"id": s["id"], "title": s["title"], "goal": s.get("goal")} for s in outline["scenes"]],
             "MOODS": music_catalog(), "LIBRARY_CUSTOM": custom_lib}
    try:
        plan_ = agent.ask_json(json.dumps(brief, ensure_ascii=False), validate=check, retries=2)
    except LLMError as e:
        log(f"  music skipped: {e}")
        return
    (out / "audio").mkdir(exist_ok=True)

    def render(t):
        mood = t["mood"]
        if mood == "custom":
            entry = "custom_" + hashlib.sha256(norm(t["prompt"]).encode()).hexdigest()[:10]
            prompt, desc = t["prompt"][:400], t["prompt"][:200]
        elif mood in MOODS:
            entry, prompt, desc = f"mood_{mood}", prompt_for(mood), MOODS[mood][1]
        else:  # an existing custom library track
            entry, prompt, desc = mood, "", lib.list("music").get(mood, {}).get("description", "")
        hit = lib.get("music", entry)
        rel = f"audio/music_{t['id']}.mp3"
        if hit:
            shutil.copyfile(hit[0], out / rel)
            lib.touch("music", entry)
            record("lyria", "music", cached=True, detail=f"library:{entry}")
            log(f"    track {t['id']}: reused library '{entry}' (no Lyria call)")
        else:
            mp3, info = process_music(make_music(prompt))
            lib.put("music", entry, mp3, "mp3", description=desc, prompt=prompt, mood=mood, seconds=info.get("seconds"))
            (out / rel).write_bytes(mp3)
            log(f"    track {t['id']}: generated '{entry}' and stored in the library: {info}")
        return {"id": t["id"], "prompt": (desc or prompt)[:400], "audio": rel,
                "volume": round(min(0.25, max(0.08, float(t.get("volume", 0.16)))), 2)}

    try:
        manifest["tracks"] = [render(t) for t in plan_["tracks"]]
    except MusicError as e:
        log(f"  music skipped: {e}")
        manifest.pop("tracks", None)
        return
    by_scene = {s["id"]: s.get("music") for s in plan_["scenes"]}
    for sc in manifest["scenes"]:
        if by_scene.get(sc["id"]):
            sc["music"] = by_scene[sc["id"]]
    log(f"  music: {plan_.get('mode')} with {len(manifest['tracks'])} track(s)")


def design_sound(manifest, out, agent):
    """Stage: sound designer decides (sparingly); sounds come from the shared library when one fits."""
    (out / "audio").mkdir(exist_ok=True)
    lib = Library()

    def fetch(prompt, reuse, rel, duration, loop):
        """Copy a library sound into the bundle, or pay for a new one and store it."""
        if reuse and lib.get("sfx", reuse):
            path, meta = lib.get("sfx", reuse)
            shutil.copyfile(path, out / rel)
            lib.touch("sfx", reuse)
            record("elevenlabs", "sfx", cached=True, detail=f"library:{reuse}")
            return meta.get("description", "")
        entry = ("amb_" if loop else "sfx_") + hashlib.sha256(norm(prompt).encode()).hexdigest()[:10]
        hit = lib.get("sfx", entry)
        if hit:
            shutil.copyfile(hit[0], out / rel)
            lib.touch("sfx", entry)
            record("elevenlabs", "sfx", cached=True, detail=f"library:{entry}")
        else:
            data = make_sfx(prompt[:200], duration, loop=loop)
            lib.put("sfx", entry, data, "mp3", description=prompt[:200], loop=loop)
            (out / rel).write_bytes(data)
        return prompt

    def one(sc):
        summary = {"music": bool(sc.get("music") and sc["music"] != "none") or bool(manifest.get("tracks")),
                   "scene": sc["title"], "language": manifest["language"],
                   "objects": [n.get("label") or n["id"] for n in sc["nodes"] if n.get("label")][:20],
                   "steps": [{"index": i, "text": st["text"]} for i, st in enumerate(sc["steps"])],
                   "LIBRARY": lib.catalog("sfx", limit=40)}

        def check(o):
            errs = []
            if not isinstance(o, dict):
                return ["reply must be an object"]
            amb = o.get("ambience")
            if amb is not None and not (amb.get("prompt") or amb.get("reuse")):
                errs.append("ambience needs a prompt, a reuse id, or null")
            sfx = o.get("sfx") or []
            if len(sfx) > 6:
                errs.append("at most 6 sfx per scene")
            idx = sorted(e.get("step", -1) for e in sfx)
            if any(i < 1 or i >= len(sc["steps"]) for i in idx):
                errs.append("sfx step must be between 1 and the last step")
            if any(b - a < 2 for a, b in zip(idx, idx[1:])):
                errs.append("sfx steps must not be consecutive")
            if any(not (e.get("prompt") or e.get("reuse")) for e in sfx):
                errs.append("each sfx needs a prompt or a reuse id")
            return errs

        try:
            plan_ = agent.ask_json(json.dumps(summary, ensure_ascii=False), validate=check, retries=2)
        except LLMError as e:
            log(f"  sound design skipped for {sc['id']}: {e}")
            return
        amb = plan_.get("ambience")
        try:
            if amb:
                rel = f"audio/{sc['id']}_ambience.mp3"
                desc = fetch((amb.get("prompt") or "")[:200], amb.get("reuse"), rel, 20.0, True)
                sc["ambience"] = {"prompt": (desc or "ambience")[:200], "audio": rel,
                                  "volume": round(min(0.25, max(0.08, float(amb.get("volume", 0.18)))), 2)}
            for e in plan_.get("sfx") or []:
                rel = f"audio/{sc['id']}_sfx{e['step']:02d}.mp3"
                desc = fetch((e.get("prompt") or "")[:200], e.get("reuse"), rel, 2.5, False)
                sc["steps"][e["step"]]["sfx"] = {"prompt": (desc or "effect")[:200], "audio": rel,
                                                  "volume": round(min(0.7, max(0.2, float(e.get("volume", 0.5)))), 2),
                                                  "delay": round(min(8.0, max(0.0, float(e.get("delay", 0.5)))), 1)}
        except SfxError as e:
            log(f"  sound effects skipped for {sc['id']}: {e}")

    with ThreadPoolExecutor(3) as ex:
        list(ex.map(one, manifest["scenes"]))
    # experience-wide budget: if over 20, drop the extra effects from the busiest scenes first
    while sum(1 for sc in manifest["scenes"] for st in sc["steps"] if st.get("sfx")) > 20:
        busiest = max(manifest["scenes"], key=lambda sc: sum(1 for st in sc["steps"] if st.get("sfx")))
        last = [st for st in busiest["steps"] if st.get("sfx")][-1]
        last.pop("sfx")
    n_amb = sum(1 for sc in manifest["scenes"] if sc.get("ambience"))
    n_fx = sum(1 for sc in manifest["scenes"] for st in sc["steps"] if st.get("sfx"))
    log(f"  sound: {n_amb} ambience beds, {n_fx} effects")


def regenerate_asset(base, out, asset_id, brief):
    """`--regen-asset ID --asset-brief TEXT --base DIR`: keep everything, remodel ONE asset (a new draft version)."""
    shutil.copytree(base, out, dirs_exist_ok=True)
    manifest = json.loads((out / "manifest.json").read_text())
    asset = next((a for a in manifest.get("assets", []) if a["id"] == asset_id), None)
    if not asset:
        sys.exit(f"unknown asset {asset_id}")
    if brief:
        asset["brief"] = brief[:700]
    log(f"1/4 remodelling {asset_id}")
    target = out / "models" / f"{asset_id}.glb"
    try:
        r = model_asset(asset["brief"], target, ROOT / ".cache" / "previews" / out.name, log=log, port=9876)
    except BlenderError as e:
        sys.exit(f"modelling failed: {e}")
    asset["file"] = f"models/{asset_id}.glb"
    asset["score"] = r["score"]
    Library().put("models", asset_id, target.read_bytes(), "glb", brief=asset["brief"], score=r["score"], accuracy=r.get("accuracy"))
    for sc in manifest["scenes"]:
        for n in sc["nodes"]:
            if n.get("asset") == asset_id:
                n["kind"], n["model"] = "model", asset["file"]
    log("4/4 validating")
    problems = validate(manifest, out)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    if problems:
        sys.exit("bundle invalid:\n- " + "\n- ".join(problems))
    log(f"OK  {out}/manifest.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("brief")
    ap.add_argument("--out", required=True)
    ap.add_argument("--voice", default=None, help="Kokoro voice id, for example af_heart or am_michael")
    ap.add_argument("--no-tts", action="store_true")
    ap.add_argument("--no-sfx", action="store_true")
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--no-models", action="store_true")
    ap.add_argument("--no-visual-repair", action="store_true", help="research ablation: local modeling without visual critic or polishing")
    ap.add_argument("--no-layout-repair", action="store_true", help="research ablation: skip automatic reframing and layout revision")
    ap.add_argument("--rebuild-assets", action="store_true", help="re-model assets even if models/*.glb exist")
    ap.add_argument("--base", help="previous bundle directory: reuse its models, and (with --revise) its plan")
    ap.add_argument("--revise", help="instructions for changing the --base experience")
    ap.add_argument("--regen-asset", help="remodel this asset id of the --base experience (nothing else changes)")
    ap.add_argument("--asset-brief", help="new description for --regen-asset")
    ap.add_argument("--scene-count", type=int, choices=range(1, 9), help="enforce the requested scene count at planning time")
    ap.add_argument("--required-asset", action="append", default=[], help="require this exact modeled-asset id in the plan")
    a = ap.parse_args()
    if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", identifier) for identifier in a.required_asset):
        ap.error("required asset identifiers must be lowercase snake_case")
    if a.base and (a.scene_count is not None or a.required_asset):
        ap.error("explicit planning constraints require a fresh brief without --base")
    if active_budget() and (not (a.no_tts and a.no_music and a.no_sfx)
                            or os.environ.get("GENOVA_VR_MODELER_URL") or a.base):
        ap.error("budget mode requires fresh local generation with --no-tts --no-music --no-sfx")
    if (a.no_visual_repair or a.no_layout_repair) and a.base:
        ap.error("repair ablations require a fresh brief without --base")
    if a.no_visual_repair and os.environ.get("GENOVA_VR_MODELER_URL"):
        ap.error("--no-visual-repair requires the local Blender backend; unset GENOVA_VR_MODELER_URL")
    emit("pipeline.repair_configuration", visual_repair=not a.no_visual_repair,
         layout_repair=not a.no_layout_repair, models_enabled=not a.no_models)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    director = Agent(ROOT / "agents" / "vr_director.json")
    if a.scene_count is not None:
        director.system += (f"\nFor this request, produce exactly {a.scene_count} scenes INCLUDING the final quiz scene. "
                            "This explicit constraint overrides the general recommendation of 3 to 6 scenes.")
    if a.required_asset:
        director.system += "\nDeclare required Blender assets with these exact IDs: " + ", ".join(a.required_asset) + "."
    designer = Agent(ROOT / "agents" / "vr_scene_designer.json")
    designer.system += ("\nRUNTIME CONTRACT: parent nodes contribute translation and opacity only. "
                        "Parent rotation and scale are NOT inherited by children. Rotating a carrier does not orbit its child; "
                        "put orbit or path motion on the child itself. For a clearly visible inclined route, use explicit "
                        "parent-relative 3D path points. Never assume a rotating parent moves a child's position.")
    designer.system += ("\nDISPLAY CONTRACT: animation clocks run for the activity lifetime, not from zero at each step. "
                        "Paths contain at most 32 points. Keep explanatory objects and labels above the bottom "
                        "head-locked caption; avoid huge luminous surfaces and coplanar transparent geometry. "
                        "Use clearly separated targets and sufficient contrast; a valid manifest alone is not a visual pass.")
    if a.no_tts:
        designer.system += ("\nAUTHORING-ONLY MODE: narration audio is disabled. Put essential show/hide/highlight "
                            "actions directly in the step-level show, hide, set, and highlight fields so the guided visual sequence remains complete. "
                            "Do not make essential visibility depend on unresolved phrase cues. Omit phrase cues "
                            "rather than inventing a speech timestamp; explicit step-relative timed cues are permitted. "
                            "A request for narration synchronization is not evaluated in this mode.")
    base = Path(a.base) if a.base else None
    if base and a.regen_asset:
        return regenerate_asset(base, out, a.regen_asset, a.asset_brief)
    if base and not a.revise:
        return upgrade_models(base, out, a)
    if base and (base / "models").exists():
        shutil.copytree(base / "models", out / "models", dirs_exist_ok=True)

    log("1/4 planning")
    previous = json.loads((base / "outline.json").read_text()) if base and (base / "outline.json").exists() else None
    outline = plan(a.brief, director, previous, a.revise, a.scene_count, a.required_asset)
    (out / "outline.json").write_text(json.dumps(outline, indent=2, ensure_ascii=False))
    log(f"  {outline['title']} ({outline['language']}): {len(outline['scenes'])} scenes")

    log("2/4 designing scenes")
    with ThreadPoolExecutor(4) as ex:
        scenes = list(ex.map(lambda i: design_scene(a.brief, outline, i, designer), range(len(outline["scenes"]))))
    manifest = {"schema": "genova.vr/1", "title": outline["title"], "language": outline["language"],
                "summary": outline.get("summary", ""),
                "assets": [{"id": x["id"], "brief": x["brief"]} for x in outline.get("assets", [])[:50]],
                "scenes": scenes}
    log("  3D models (Blender)" if not a.no_models else "  3D models skipped")
    build_assets(manifest, out, skip=a.no_models, rebuild=a.rebuild_assets, visual_repair=not a.no_visual_repair)

    for sc in manifest["scenes"]:
        for w in effects.expand_scene(sc):
            log(f"  effects ({sc['id']}): {w}")
    log("  layout review")
    if not a.no_layout_repair:
        review_layout(manifest, outline, designer)
    else:
        emit("layout.disabled")
        log("  layout repair disabled (research ablation)")
    if a.no_tts:
        removed_cues = discard_unresolved_cues(manifest)
        for cue in removed_cues:
            emit("cue.unresolved_removed", **cue)
        log(f"  {len(removed_cues)} unresolved narration cues omitted (no TTS); explicit timed cues retained")

    log("3/4 sky + narration")
    add_sky(manifest, out)
    if not a.no_tts:
        try:
            narrate(manifest, out, a.voice)
        except TTSError as e:
            sys.exit(f"TTS failed: {e}")

    if not a.no_music:
        log("  soundtrack (Lyria)")
        design_music(manifest, outline, out, Agent(ROOT / "agents" / "vr_composer.json"))
    if not a.no_sfx:
        log("  sound design (ElevenLabs)")
        design_sound(manifest, out, Agent(ROOT / "agents" / "vr_sound_designer.json"))

    log("4/4 validating")
    problems = validate(manifest, out)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    if problems:
        sys.exit("bundle invalid:\n- " + "\n- ".join(problems))
    log(f"OK  {out}/manifest.json")


if __name__ == "__main__":
    try:
        main()
    except (LLMError, BudgetError) as e:
        sys.exit(f"LLM error: {e}")
