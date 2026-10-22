"""Blender modeler stage: brief -> .glb, with a render-and-critique loop.

modeler agent writes bpy code -> Blender bridge runs it and renders 4 views -> critic agent (vision)
reviews -> feedback goes back to the modeler (up to MAX_ROUNDS). Works for any subject.
"""
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nodes"))

from blender_client import Blender, BlenderError  # noqa: E402
from library import CapExceeded, check_cap, record  # noqa: E402
from llm import Agent, extract_json  # noqa: E402
from experiment_trace import emit
from api_budget import block_unmetered

PRELUDE = (ROOT / "nodes" / "blender_prelude.py").read_text()
MAX_ROUNDS = 3
MAX_POLISH = 2
MAX_TRIANGLES = 30000
MAX_BYTES = 4_000_000


def _code(reply: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", reply, re.S)
    return (m.group(1) if m else reply).strip()


def _http_asset(brief, out_glb, log, height=None):
    """Build the asset on the Cloud Run modeler service (GPU, scales to zero)."""
    import urllib.error
    import urllib.request
    block_unmetered("remote GPU modeling")
    try:
        check_cap("cloudrun_gpu")
    except CapExceeded as e:
        raise BlenderError(str(e)) from e
    url = os.environ["GENOVA_VR_MODELER_URL"].rstrip("/") + "/model"
    token = os.environ["GENOVA_VR_MODELER_TOKEN"]
    body = json.dumps({"brief": brief, "height": height}).encode()
    last = None
    for attempt in range(4):
        req = urllib.request.Request(url, data=body, headers={"Authorization": "Bearer " + token,
                                                                "Content-Type": "application/json"})
        try:
            log(f"  asking the 3D service: {out_glb.stem}")
            with urllib.request.urlopen(req, timeout=3400) as r:
                data = r.read()
                score = float(r.headers.get("X-Score", 0))
                accuracy = float(r.headers.get("X-Accuracy", score))
                rounds = int(r.headers.get("X-Rounds", 0))
            out_glb.parent.mkdir(parents=True, exist_ok=True)
            out_glb.write_bytes(data)
            record("cloudrun_gpu", "model", cached=False, detail=out_glb.stem)
            return {"glb": str(out_glb), "score": score, "accuracy": accuracy, "rounds": rounds}
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read()[:200]!r}"
            if e.code in (400, 401, 403):
                break
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(e)
        time.sleep(10 * (attempt + 1))  # 429/503 while instances start or the quota is busy
    raise BlenderError(f"3D service failed: {last}")


def model_asset(brief: str, out_glb: Path, preview_dir: Path | None = None, height=None, log=print, port=9876,
                visual_repair=True) -> dict:
    """Build one asset: build loop (modeler + critic), then a polish layer (refiner + critic).

    Returns {"glb", "score", "accuracy", "rounds"}. Raises BlenderError if nothing usable results.
    """
    if os.environ.get("GENOVA_VR_MODELER_URL"):
        if not visual_repair:
            raise BlenderError("visual-repair ablation requires the local Blender backend")
        return _http_asset(brief, out_glb, log, height)
    modeler = Agent(ROOT / "agents" / "vr_modeler.json")
    critic = Agent(ROOT / "agents" / "vr_model_critic.json") if visual_repair else None
    refiner = Agent(ROOT / "agents" / "vr_model_refiner.json") if visual_repair else None
    out_glb.parent.mkdir(parents=True, exist_ok=True)
    preview_dir = preview_dir or out_glb.parent
    best = None  # dict(score, accuracy, data, code, review)
    build_attempts = 0
    polish_attempts = 0
    critic_calls = 0

    def evaluate(bl, code, tag):
        """Run a script in Blender, tidy, render 6 views, get the critic's review, export the glb."""
        nonlocal critic_calls
        bl.call("reset_scene")
        bl.call("execute_code", code=PRELUDE + "\n" + code)
        bl.call("cleanup")
        info = bl.call("get_scene_info")
        tris = sum(o.get("triangles", 0) for o in info["objects"])
        if tris > MAX_TRIANGLES:
            raise BlenderError(f"{tris} triangles exceeds the {MAX_TRIANGLES} budget: reduce segment counts and subdivision")
        sheet = preview_dir / f"{out_glb.stem}_{tag}.png"
        shot = bl.call("get_viewport_screenshot", filepath=str(sheet), max_size=448)
        if visual_repair:
            critic_calls += 1
            emit("modeler.critic", asset=out_glb.stem, phase=tag, call=critic_calls)
            reply = critic.ask(f"BRIEF:\n{brief}\n\nRender stats: {tris} triangles, objects: "
                               f"{[o['name'] for o in info['objects']]}, dimensions (m): "
                               f"{[o.get('dimensions') for o in info['objects']][:3]}. Animation: {info.get('animation')}", images=[str(sheet)])
            rev = extract_json(reply)
            score = float(rev.get("score", 0))
            acc = float(rev.get("accuracy", score))
        else:
            rev, score, acc = {}, None, None
        glb = bl.call("export_glb", filepath=str(out_glb))
        data = out_glb.read_bytes()
        emit("modeler.candidate", asset=out_glb.stem, phase=tag, triangles=tris,
             bytes=len(data), score=score, accuracy=acc, visual_repair=visual_repair)
        assessment = f"critic {score}/10, accuracy {acc}/10" if visual_repair else "visual critic disabled"
        log(f"    {tag}: {tris} tris, {glb['bytes'] // 1024} KB, {assessment} ({shot.get('device')})")
        return {"score": score, "accuracy": acc, "data": data, "code": code, "review": rev}

    def consider(res):
        nonlocal best
        if len(res["data"]) > MAX_BYTES:
            return
        if not visual_repair:
            if best is None:
                best = res
            return
        key = lambda r: (r["score"] + r["accuracy"])  # noqa: E731 - both matter
        if best is None or key(res) > key(best):
            best = res

    def ok(res):
        return res["review"].get("ok") or (res["score"] >= 8 and res["accuracy"] >= 8)

    with Blender(port=port) as bl:
        # ---- phase 1: build until the critic is satisfied
        feedback, code = None, None
        for rnd in range(1, MAX_ROUNDS + 1):
            build_attempts += 1
            emit("modeler.build_attempt", asset=out_glb.stem, attempt=build_attempts,
                 visual_repair=visual_repair)
            q = f"BRIEF:\n{brief}\n"
            if height:
                q += f"\nTarget height: {height} m (pass it to finish()).\n"
            if feedback:
                feedback_label = f"ART CRITIC FEEDBACK (score {feedback['score']}/10)" if visual_repair else "EXECUTION/RESOURCE FEEDBACK"
                q += (f"\nYOUR PREVIOUS SCRIPT:\n```python\n{code}\n```\n\n{feedback_label}:\n"
                      f"Problems: {feedback.get('problems')}\nFixes: {feedback.get('fixes')}\n")
            code = _code(modeler.ask(q, use_cache=(rnd == 1)))
            try:
                res = evaluate(bl, code, f"build{rnd}")
            except BlenderError as e:
                log(f"    build{rnd}: script error")
                feedback = {"score": 2, "problems": ["the script failed or broke the budget"], "fixes": str(e)[-800:]}
                continue
            consider(res)
            if not visual_repair:
                if best is not None:
                    break
                feedback = {"score": 2, "problems": ["exported asset exceeds the byte budget"],
                            "fixes": f"Reduce geometry and textures below {MAX_BYTES} bytes."}
                continue
            if ok(res):
                break
            feedback = {"score": res["score"], "problems": res["review"].get("problems"), "fixes": res["review"].get("fixes")}
        if best is None:
            emit("modeler.failed", asset=out_glb.stem, build_attempts=build_attempts,
                 polish_attempts=polish_attempts, critic_calls=critic_calls)
            raise BlenderError(f"no usable model produced for: {brief[:80]}")

        # ---- phase 2: polish layer. Improve the best version; keep a change only if the critic scores it higher.
        # Adaptive: a good model (>= 7/7) gets one polish attempt, a weaker one gets two; stop at the first
        # attempt that does not improve it (it rarely helps later and costs ~6 minutes of GPU time).
        max_polish = (1 if best["score"] >= 7 and best["accuracy"] >= 7 else MAX_POLISH) if visual_repair else 0
        for pr in range(1, max_polish + 1):
            if best["score"] >= 9 and best["accuracy"] >= 9:
                break
            polish_attempts += 1
            emit("modeler.polish_attempt", asset=out_glb.stem, attempt=polish_attempts)
            rv = best["review"]
            q = (f"BRIEF:\n{brief}\n\nCURRENT SCRIPT:\n```python\n{best['code']}\n```\n\n"
                 f"ART DIRECTOR NOTES (score {best['score']}/10, accuracy {best['accuracy']}/10):\n"
                 f"Strengths to preserve: {rv.get('strengths')}\nProblems to fix: {rv.get('problems')}\n"
                 f"Fixes: {rv.get('fixes')}\nPolish ideas: {rv.get('polish')}\n")
            if height:
                q += f"\nTarget height: {height} m.\n"
            try:
                res = evaluate(bl, _code(refiner.ask(q, use_cache=False)), f"polish{pr}")
            except BlenderError:
                log(f"    polish{pr}: failed, keeping the previous version")
                continue
            before = best
            consider(res)
            if best is before:
                log(f"    polish{pr}: not better ({res['score']}/{res['accuracy']}), keeping the previous version")
                break
    out_glb.write_bytes(best["data"])
    emit("modeler.completed", asset=out_glb.stem, build_attempts=build_attempts,
         polish_attempts=polish_attempts, critic_calls=critic_calls, visual_repair=visual_repair)
    return {"glb": str(out_glb), "score": best["score"], "accuracy": best["accuracy"],
            "rounds": build_attempts, "polish_rounds": polish_attempts, "critic_calls": critic_calls}
