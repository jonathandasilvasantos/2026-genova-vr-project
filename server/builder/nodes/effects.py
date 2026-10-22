"""Special effects: particles, dynamic lights, fog. Subject-independent presets, expanded to explicit parameters
so the player only ever sees complete, validated values.

Manifest vocabulary (see schema/bundle.schema.json):
  scene.fog                 {"color": [r,g,b], "density": 0.03, "base": 0, "falloff": 0.2, "start": 2}
  node kind "emitter"       {"emitter": {"preset": "...", ...overrides}}
  node kind "light"         {"light": {"type": "point"|"spot", ...}}
  node kind "effect"        {"effect": "explosion"|"campfire"}   -> expanded here into emitters + a light
  step / cue actions        "burst": [ids]   fires a one-shot emitter/light (or every emitter inside an effect)
Budgets (the viewer is a phone in a headset; transparency is the expensive part): see LIMITS.
"""
import copy

LIMITS = {"lights": 4, "emitters": 6, "particles": 1500, "max_count": 400}

EMITTER_DEFAULTS = {
    "count": 60, "life": 2.0, "speed": 1.0, "gravity": 0.0, "drag": 0.0, "spread": 45.0,
    "dir": [0, 1, 0], "shape": "point", "extent": [0.0, 0.0, 0.0], "size": [0.1, 0.1],
    "color_start": [1, 1, 1, 1], "color_end": [1, 1, 1, 0], "blend": "additive", "loop": True, "texture": "soft",
}

PRESETS = {
    "sparks": {"count": 80, "life": 1.2, "speed": 3.0, "gravity": -6.0, "drag": 0.3, "spread": 60, "size": [0.05, 0.01],
               "color_start": [1, 0.85, 0.35, 1], "color_end": [1, 0.3, 0.05, 0], "texture": "spark"},
    "fire": {"count": 70, "life": 1.1, "speed": 0.9, "gravity": 1.5, "drag": 0.2, "spread": 18, "shape": "disc",
             "extent": [0.15, 0, 0.15], "size": [0.35, 0.05], "color_start": [1, 0.75, 0.2, 0.9], "color_end": [1, 0.2, 0.0, 0], "texture": "soft"},
    "smoke": {"count": 40, "life": 3.5, "speed": 0.5, "gravity": 0.6, "drag": 0.4, "spread": 25, "shape": "disc",
              "extent": [0.2, 0, 0.2], "size": [0.25, 1.0], "color_start": [0.55, 0.55, 0.55, 0.35], "color_end": [0.2, 0.2, 0.2, 0],
              "blend": "alpha", "texture": "smoke"},
    "steam": {"count": 36, "life": 3.0, "speed": 0.7, "gravity": 0.5, "drag": 0.5, "spread": 22, "shape": "disc",
              "extent": [0.1, 0, 0.1], "size": [0.2, 0.8], "color_start": [0.95, 0.97, 1, 0.3], "color_end": [1, 1, 1, 0],
              "blend": "alpha", "texture": "smoke"},
    "dust": {"count": 90, "life": 6.0, "speed": 0.12, "spread": 180, "shape": "box", "extent": [3, 1.5, 3], "size": [0.035, 0.035],
             "color_start": [0.95, 0.9, 0.8, 0.5], "color_end": [0.95, 0.9, 0.8, 0], "texture": "soft"},
    "explosion": {"count": 140, "life": 1.6, "speed": 5.0, "gravity": -2.0, "drag": 1.2, "spread": 180, "shape": "sphere",
                  "extent": [0.1, 0.1, 0.1], "size": [0.5, 0.1], "color_start": [1, 0.7, 0.2, 1], "color_end": [0.8, 0.1, 0, 0],
                  "loop": False, "texture": "soft"},
    "rain": {"count": 220, "life": 1.0, "speed": 9.0, "gravity": -9.0, "spread": 4, "dir": [0, -1, 0], "shape": "box",
             "extent": [4, 0, 4], "size": [0.018, 0.018], "color_start": [0.7, 0.8, 1, 0.7], "color_end": [0.7, 0.8, 1, 0.2], "texture": "spark"},
    "snow": {"count": 160, "life": 5.0, "speed": 0.5, "spread": 25, "dir": [0, -1, 0], "shape": "box", "extent": [4, 0, 4],
             "size": [0.05, 0.05], "color_start": [1, 1, 1, 0.9], "color_end": [1, 1, 1, 0.4], "texture": "soft"},
    "bubbles": {"count": 40, "life": 5.0, "speed": 0.4, "gravity": 0.1, "spread": 20, "shape": "box", "extent": [1.5, 0, 1.5],
                "size": [0.08, 0.1], "color_start": [0.7, 0.9, 1, 0.8], "color_end": [0.7, 0.9, 1, 0], "texture": "ring"},
    "magic": {"count": 70, "life": 2.5, "speed": 0.6, "gravity": 0.3, "drag": 0.2, "spread": 180, "shape": "sphere",
              "extent": [0.4, 0.4, 0.4], "size": [0.07, 0.01], "color_start": [0.7, 0.55, 1, 1], "color_end": [0.4, 0.9, 1, 0], "texture": "star"},
    "mist": {"count": 16, "life": 10.0, "speed": 0.05, "spread": 180, "shape": "box", "extent": [6, 0.5, 6], "size": [2.0, 3.0],
             "color_start": [0.8, 0.85, 0.9, 0.10], "color_end": [0.8, 0.85, 0.9, 0], "blend": "alpha", "texture": "smoke"},
}

LIGHT_DEFAULTS = {"type": "point", "color": [1, 1, 1], "intensity": 1.0, "range": 8.0, "angle": 35.0, "softness": 0.3,
                  "dir": [0, -1, 0], "flicker": "none", "beam": False}
LIGHT_PRESETS = {
    "candle": {"color": [1, 0.65, 0.3], "intensity": 1.2, "range": 5, "flicker": "candle"},
    "torch": {"color": [1, 0.6, 0.25], "intensity": 1.8, "range": 9, "flicker": "candle"},
    "lamp": {"color": [1, 0.92, 0.75], "intensity": 1.5, "range": 10},
    "spotlight": {"type": "spot", "color": [1, 1, 0.95], "intensity": 2.5, "range": 14, "angle": 28, "beam": True},
    "moonlight": {"color": [0.6, 0.7, 1], "intensity": 0.9, "range": 20},
    "alarm": {"color": [1, 0.15, 0.1], "intensity": 2.0, "range": 8, "flicker": "strobe"},
}

# Compound effects: expanded into plain emitter/light nodes parented to a group node.
MACROS = {
    "explosion": [
        ("fireball", "emitter", {"preset": "explosion"}),
        ("sparks", "emitter", {"preset": "sparks", "loop": False, "count": 70, "speed": 6.0}),
        ("smoke", "emitter", {"preset": "smoke", "loop": False, "count": 28, "life": 3.2, "speed": 1.4}),
        ("flash", "light", {"color": [1, 0.75, 0.4], "intensity": 4.5, "range": 12, "flicker": "flash"}),
    ],
    "campfire": [
        ("flames", "emitter", {"preset": "fire"}),
        ("embers", "emitter", {"preset": "sparks", "count": 24, "speed": 1.6, "spread": 25}),
        ("smoke", "emitter", {"preset": "smoke", "count": 22, "extent": [0.12, 0, 0.12]}),
        ("glow", "light", {"preset": "torch", "intensity": 1.6}),
    ],
}


def emitter_params(spec):
    p = copy.deepcopy(EMITTER_DEFAULTS)
    p.update(copy.deepcopy(PRESETS.get(spec.get("preset", ""), {})))
    p.update({k: v for k, v in spec.items() if k != "preset"})
    p["count"] = max(1, min(int(p["count"]), LIMITS["max_count"]))
    return p


def light_params(spec):
    p = copy.deepcopy(LIGHT_DEFAULTS)
    p.update(copy.deepcopy(LIGHT_PRESETS.get(spec.get("preset", ""), {})))
    p.update({k: v for k, v in spec.items() if k != "preset"})
    return p


def expand_scene(scene):
    """Replace `effect` macro nodes by their parts and fill every emitter/light with explicit parameters.
    Returns a list of warnings (budget overruns that were trimmed)."""
    warnings = []
    out = []
    for n in scene["nodes"]:
        if n["kind"] == "effect":
            name = n.get("effect", "explosion")
            parts = MACROS.get(name)
            if not parts:
                warnings.append(f"unknown effect '{name}'")
                continue
            group = {k: v for k, v in n.items() if k not in ("effect", "kind")}
            group.update({"kind": "sphere", "scale": 0.01, "color": [1, 1, 1, 0], "lit": False, "effect_group": True})
            out.append(group)
            for pid, kind, spec in parts:
                child = {"id": f"{n['id']}_{pid}"[:41], "kind": kind, "parent": n["id"], "pos": [0, 0, 0]}
                child[kind] = copy.deepcopy(spec)
                out.append(child)
        else:
            out.append(n)
    nodes = []
    for n in out:
        if n["kind"] == "emitter":
            n["emitter"] = emitter_params(n.get("emitter", {}))
        elif n["kind"] == "light":
            n["light"] = light_params(n.get("light", {}))
        nodes.append(n)
    # budgets: keep the first lights/emitters, scale particle counts down proportionally
    lights = [n for n in nodes if n["kind"] == "light"]
    ems = [n for n in nodes if n["kind"] == "emitter"]
    if len(lights) > LIMITS["lights"]:
        warnings.append(f"{len(lights)} lights; only the first {LIMITS['lights']} are kept")
        drop = {id(n) for n in lights[LIMITS["lights"]:]}
        nodes = [n for n in nodes if id(n) not in drop]
    if len(ems) > LIMITS["emitters"]:
        warnings.append(f"{len(ems)} emitters; only the first {LIMITS['emitters']} are kept")
        drop = {id(n) for n in ems[LIMITS["emitters"]:]}
        nodes = [n for n in nodes if id(n) not in drop]
        ems = ems[:LIMITS["emitters"]]
    total = sum(n["emitter"]["count"] for n in ems)
    if total > LIMITS["particles"]:
        k = LIMITS["particles"] / total
        for n in ems:
            n["emitter"]["count"] = max(1, int(n["emitter"]["count"] * k))
        warnings.append(f"{total} particles trimmed to {LIMITS['particles']}")
    scene["nodes"] = nodes
    return warnings
