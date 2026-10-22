"""Schema + semantic validation for a Genova VR bundle manifest."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "schema" / "bundle.schema.json").read_text())
_validator = Draft202012Validator(SCHEMA)

MAX_TOTAL_NODES = 80
MAX_DISTANCE = 400.0
MAX_ASSETS = 50


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def validate(manifest: dict, bundle_dir: Path | None = None) -> list[str]:
    """Return a list of human-readable problems (empty list means valid)."""
    errs = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in sorted(_validator.iter_errors(manifest), key=lambda e: list(e.absolute_path))]
    if errs:
        return errs[:20]
    track_ids = [t["id"] for t in manifest.get("tracks", [])]
    if len(track_ids) != len(set(track_ids)):
        errs.append("duplicate track ids")
    if len(track_ids) > 3:
        errs.append("at most 3 music tracks per experience")
    asset_ids = [a["id"] for a in manifest.get("assets", [])]
    if len(asset_ids) != len(set(asset_ids)):
        errs.append("duplicate asset ids")
    if len(asset_ids) > MAX_ASSETS:
        errs.append(f"at most {MAX_ASSETS} modeled assets per experience")
    seen_scenes = set()
    for sc in manifest["scenes"]:
        sid = sc["id"]
        if sid in seen_scenes:
            errs.append(f"duplicate scene id {sid}")
        seen_scenes.add(sid)
        ids, order = {}, []
        for i, n in enumerate(sc["nodes"]):
            if n["id"] in ids:
                errs.append(f"{sid}: duplicate node id {n['id']}")
            if n.get("parent") and n["parent"] not in ids:
                errs.append(f"{sid}: node {n['id']} parent {n['parent']} must be defined BEFORE it")
            if n.get("parent") == n["id"]:
                errs.append(f"{sid}: node {n['id']} is its own parent")
            ids[n["id"]] = n
            if n.get("orbit", {}).get("radius", 0) > 0 and not n.get("parent") and n.get("pos") is None:
                pass
            if "pos" in n and _dist(n["pos"], [0, 0, 0]) > MAX_DISTANCE:
                errs.append(f"{sid}: node {n['id']} is farther than {MAX_DISTANCE} m")
            if n["kind"] == "emitter" and not isinstance(n.get("emitter"), dict):
                errs.append(f"{sid}: emitter node {n['id']} needs an `emitter` object (a preset or parameters)")
            if n["kind"] == "light" and not isinstance(n.get("light"), dict):
                errs.append(f"{sid}: light node {n['id']} needs a `light` object")
            if n["kind"] == "effect" and not n.get("effect"):
                errs.append(f"{sid}: effect node {n['id']} needs `effect` (explosion or campfire)")
            if isinstance(n.get("light"), dict) and n["light"].get("aim") and n["light"]["aim"] not in ids and n["light"]["aim"] != n["id"]:
                errs.append(f"{sid}: light {n['id']} aims at unknown node {n['light']['aim']}")
            if n["kind"] == "model" and not (n.get("model") or n.get("asset")):
                errs.append(f"{sid}: model node {n['id']} needs `asset` (an id from the experience assets)")
            if n.get("asset") and n["asset"] not in asset_ids:
                errs.append(f"{sid}: node {n['id']} uses unknown asset {n['asset']}; allowed: {asset_ids}")
            if n.get("model") and bundle_dir and not (bundle_dir / n["model"]).exists():
                errs.append(f"{sid}: model file missing: {n['model']}")
            if n["kind"] == "quad" and not (n.get("texture") or n.get("label")):
                errs.append(f"{sid}: quad {n['id']} needs texture or label")
            tex = n.get("texture")
            if tex and bundle_dir and not (bundle_dir / tex).exists():
                errs.append(f"{sid}: texture file missing: {tex}")
        if sum(1 for n in sc["nodes"] if n.get("light") is True) > 1:
            errs.append(f"{sid}: at most one node may have light=true")
        for k, st in enumerate(sc["steps"]):
            cam = st.get("camera") or {}
            for ref in ("focus", "follow"):
                if cam.get(ref) and cam[ref] not in ids:
                    errs.append(f"{sid}: step {k} camera.{ref} unknown node {cam[ref]}")
            for key in ("show", "hide", "burst"):
                for ref in st.get(key, []):
                    if ref not in ids:
                        errs.append(f"{sid}: step {k} {key} unknown node {ref}")
            for op in st.get("set", []):
                if op["node"] not in ids:
                    errs.append(f"{sid}: step {k} set unknown node {op['node']}")
            for cue in st.get("cues", []):
                if cue.get("highlight") and cue["highlight"] not in ids:
                    errs.append(f"{sid}: step {k} cue highlight unknown node {cue['highlight']}")
                for key in ("show", "hide", "burst"):
                    for ref in cue.get(key, []):
                        if ref not in ids:
                            errs.append(f"{sid}: step {k} cue {key} unknown node {ref}")
                for op in cue.get("set", []):
                    if op["node"] not in ids:
                        errs.append(f"{sid}: step {k} cue set unknown node {op['node']}")
                if not cue.get("phrase") and cue.get("at") is None:
                    errs.append(f"{sid}: step {k} cue needs a phrase or an `at` time")
                if cue.get("phrase") and cue["phrase"].lower().strip(" .,;:!?") not in st["text"].lower():
                    errs.append(f"{sid}: step {k} cue phrase '{cue['phrase']}' is not in the step text; copy 1 to 4 words exactly")
            if st.get("highlight") and st["highlight"] not in ids:
                errs.append(f"{sid}: step {k} highlight unknown node {st['highlight']}")
            if cam.get("follow"):
                errs.append(f"{sid}: step {k} camera.follow is not allowed: the viewer must never be carried; use a fixed camera.pos")
            if cam.get("follow") and not cam.get("pos"):
                errs.append(f"{sid}: step {k} camera.follow needs pos (offset from the node)")
            if not cam.get("follow") and cam.get("pos") and abs(cam["pos"][1]) > 30:
                errs.append(f"{sid}: step {k} camera y is absurd ({cam['pos'][1]})")
        if sc.get("music") not in (None, "none") and sc["music"] not in track_ids:
            errs.append(f"{sid}: music refers to unknown track {sc['music']}")
        n_sfx = sum(1 for st in sc["steps"] if st.get("sfx"))
        if n_sfx > 6:
            errs.append(f"{sid}: at most 6 sound effects per scene (found {n_sfx})")
        for k, st in enumerate(sc["steps"]):
            if st.get("sfx") and k == 0:
                errs.append(f"{sid}: no sound effect on the first step")
            if st.get("sfx") and k > 0 and sc["steps"][k - 1].get("sfx"):
                errs.append(f"{sid}: sound effects on consecutive steps {k - 1},{k}")
        q = sc.get("quiz")
        if q and sum(1 for o in q["options"] if o.get("correct")) != 1:
            errs.append(f"{sid}: quiz needs exactly one correct option")
    total_sfx = sum(1 for sc in manifest["scenes"] for st in sc["steps"] if st.get("sfx"))
    if total_sfx > 20:
        errs.append(f"at most 20 sound effects per experience (found {total_sfx})")
    if bundle_dir:
        for t in manifest.get("tracks", []):
            if t.get("audio") and not (bundle_dir / t["audio"]).exists():
                errs.append(f"track {t['id']}: file missing: {t['audio']}")
        for sc in manifest["scenes"]:
            files = [s.get("sfx", {}).get("audio") for s in sc["steps"]] + [sc.get("ambience", {}).get("audio")]
            for f in filter(None, files):
                if not (bundle_dir / f).exists():
                    errs.append(f"{sc['id']}: sound file missing: {f}")
    return errs
