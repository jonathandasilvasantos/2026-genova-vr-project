# Helpers injected into the Blender namespace before the modeler's script runs.
import math
import bpy
import numpy as np


def _principled(m):
    m.use_nodes = True
    return m.node_tree.nodes["Principled BSDF"]


def mat(name, rgb, metal=0.0, rough=0.5, emit=None, emit_strength=1.0):
    m = bpy.data.materials.new(name)
    b = _principled(m)
    b.inputs["Base Color"].default_value = (*rgb[:3], 1)
    b.inputs["Metallic"].default_value = metal
    b.inputs["Roughness"].default_value = rough
    if emit is not None:
        b.inputs["Emission Color"].default_value = (*emit[:3], 1)
        b.inputs["Emission Strength"].default_value = emit_strength
    return m


def noise(u, v, scale=6.0, octaves=5, seed=0):
    """Seamless-in-u fbm value noise in 0..1 on equirectangular coordinates."""
    rng = np.random.default_rng(seed)
    out = np.zeros_like(u)
    amp, norm = 1.0, 0.0
    for o in range(octaves):
        n = max(2, int(scale * (2 ** o)))
        grid = rng.random((n + 1, n + 1))
        grid[:, -1] = grid[:, 0]  # wrap longitude
        x = (u % 1.0) * n
        y = np.clip(v, 0, 1) * n
        x0, y0 = np.floor(x).astype(int), np.minimum(np.floor(y).astype(int), n - 1)
        fx, fy = x - x0, y - y0
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        x1 = x0 + 1
        val = (grid[y0, x0] * (1 - fx) + grid[y0, x1] * fx) * (1 - fy) + \
              (grid[y0 + 1, x0] * (1 - fx) + grid[y0 + 1, x1] * fx) * fy
        out += val * amp
        norm += amp
        amp *= 0.5
    return out / norm


def mix(c0, c1, t):
    c0, c1 = np.asarray(c0, float), np.asarray(c1, float)
    t = np.asarray(t, float)[..., None]
    return c0 * (1 - t) + c1 * t


def tex_mat(name, fn, size=512, rough=0.6, metal=0.0):
    w, h = size, size // 2
    u, v = np.meshgrid((np.arange(w) + 0.5) / w, (np.arange(h) + 0.5) / h)
    rgb = np.clip(np.asarray(fn(u, v), dtype=float), 0, 1)
    px = np.ones((h, w, 4), dtype=np.float32)
    px[..., :3] = rgb
    img = bpy.data.images.new(name + "_tex", w, h)
    img.pixels = px.ravel().tolist()
    img.pack()
    m = bpy.data.materials.new(name)
    b = _principled(m)
    tex = m.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = img
    m.node_tree.links.new(tex.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    return m


def assign(obj, material):
    obj.data.materials.clear()
    obj.data.materials.append(material)


def smooth(obj):
    for p in obj.data.polygons:
        p.use_smooth = True


FPS = 24
MAX_CLIP_SECONDS = 24


def _setup_keys(interp="BEZIER"):
    bpy.context.preferences.edit.keyframe_new_interpolation_type = interp


def _register(obj, prop, keys, period, interp, cycle_offset=None):
    """Record one looping cycle (keys[0] at t=0 ... keys[-1] at t=period). finish() later writes the keyframes,
    repeating each cycle so that objects with different periods share one seamless clip."""
    specs = list(obj.get("gv_specs", []))
    specs = [sp for sp in specs if sp["prop"] != prop]
    specs.append({"prop": prop, "keys": [list(k) for k in keys], "period": float(period), "interp": interp,
                  "offset": list(cycle_offset) if cycle_offset else None})
    obj["gv_specs"] = specs
    obj.keyframe_insert(data_path=prop, frame=1)  # makes the action exist right away (preview before finish)


def _vec(k):
    return tuple(k) if hasattr(k, "__len__") else (k, k, k)


def animate(obj, prop, keys, period, interp="BEZIER"):
    """Generic looping animation of `prop` ('location', 'rotation_euler' or 'scale'): `keys` are vectors (or
    numbers for a uniform scale) spread evenly over `period` seconds; the first key is appended at the end so
    the loop is seamless. Use it for anything that moves in a cycle: gears, valves, wheels, flows, plants."""
    keys = [_vec(k) for k in keys]
    _register(obj, prop, keys + [keys[0]], period, interp)
    return obj


def spin(obj, axis="Z", period=4.0, turns=1, reverse=False):
    """Rotate continuously about the object's own origin (wheel, gear, rotor, planet, turbine, drill)."""
    i = "XYZ".index(axis.upper())
    base = list(obj.rotation_euler)
    sign = -1 if reverse else 1
    keys = []
    for k in range(5):
        v = list(base)
        v[i] = base[i] + sign * 2 * math.pi * turns * k / 4
        keys.append(v)
    off = [0.0, 0.0, 0.0]
    off[i] = sign * 2 * math.pi * turns
    _register(obj, "rotation_euler", keys, period, "LINEAR", cycle_offset=off)
    return obj


def swing(obj, axis="Y", degrees=30.0, period=2.0, phase=0.0):
    """Oscillate back and forth about an axis through the object's origin (valve flap, pendulum, door, wing,
    lever, arm). Put the object's origin at the hinge."""
    i = "XYZ".index(axis.upper())
    base = list(obj.rotation_euler)
    amp = math.radians(degrees)
    keys = []
    for k in range(8):
        v = list(base)
        v[i] = base[i] + amp * math.sin(2 * math.pi * (k / 8 + phase))
        keys.append(v)
    return animate(obj, "rotation_euler", keys, period, "BEZIER")


def pulse(obj, amount=0.08, period=1.0, beats=1):
    """Rhythmic grow/shrink about the object's origin (heartbeat, breathing, glowing, a swelling cell)."""
    base = tuple(obj.scale)
    keys = []
    for k in range(8 * beats):
        t = (k / (8 * beats)) * beats % 1.0
        f = 1 + amount * max(0.0, math.sin(2 * math.pi * t)) ** 2
        keys.append(tuple(c * f for c in base))
    return animate(obj, "scale", keys, period, "BEZIER")


def bob(obj, axis="Z", amount=0.1, period=2.0):
    """Gentle back-and-forth translation (floating, a piston stroke, a boat on water, a bouncing ball)."""
    i = "XYZ".index(axis.upper())
    base = list(obj.location)
    keys = []
    for k in range(8):
        v = list(base)
        v[i] = base[i] + amount * math.sin(2 * math.pi * k / 8)
        keys.append(v)
    return animate(obj, "location", keys, period, "BEZIER")


def travel(obj, points, period=4.0):
    """Move along a closed loop of points (blood in a vessel, a car on a road, current in a wire, a particle).
    Points are [x, y, z] in the object's parent space; the object returns to the first point at the end."""
    return animate(obj, "location", [tuple(p) for p in points], period, "LINEAR")


def grow(obj, from_scale=0.2, to_scale=1.0, period=4.0):
    """Scale up and back down smoothly (a seed becoming a plant, a cell dividing, a balloon filling)."""
    b = tuple(obj.scale)
    return animate(obj, "scale", [tuple(c * from_scale for c in b), tuple(c * to_scale for c in b),
                                   tuple(c * to_scale for c in b), tuple(c * from_scale for c in b)], period, "BEZIER")


def _bake_animations():
    """Write all registered cycles as keyframes over ONE shared clip length (the least common multiple of the
    periods, capped; otherwise periods are snapped to divide the longest one)."""
    from math import gcd
    sc = bpy.context.scene
    objs = [o for o in sc.objects if o.get("gv_specs")]
    if not objs:
        return None
    sc.render.fps = FPS
    frames = sorted({max(2, int(round(sp["period"] * FPS))) for o in objs for sp in o["gv_specs"]})
    total = 1
    for f in frames:
        total = total * f // gcd(total, f)
    cap = MAX_CLIP_SECONDS * FPS
    longest = frames[-1]
    snapped = {}
    if total > cap:
        total = longest
    for f in frames:
        snapped[f] = f if total % f == 0 else max(2, round(longest / max(1, round(longest / f))))
        if total % snapped[f] != 0:
            snapped[f] = longest
    for o in objs:
        o.animation_data_clear()
        for sp in o["gv_specs"]:
            n = snapped[max(2, int(round(sp["period"] * FPS)))]
            _setup_keys("LINEAR" if sp["interp"] == "LINEAR" else "BEZIER")
            m = len(sp["keys"])
            for rep in range(total // n):
                for i, k in enumerate(sp["keys"]):
                    off = sp["offset"]
                    v = [k[c] + (off[c] * rep if off else 0.0) for c in range(3)]
                    setattr(o, sp["prop"], v)
                    o.keyframe_insert(data_path=sp["prop"], frame=1 + rep * n + round(n * i / (m - 1)))
            o.rotation_euler = o["gv_specs"][0]["keys"][0] if sp["prop"] == "rotation_euler" else o.rotation_euler
    sc.frame_start, sc.frame_end = 1, 1 + total
    return total / FPS


def _world_bounds(objs):
    pts = []
    for o in objs:
        pts += [o.matrix_world @ Vector(c) for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def finish(height=None):
    """Join the STATIC meshes into one object, keep ANIMATED objects separate (their keyframes are preserved),
    put everything under one root that is centred on the origin (and scaled to `height` metres if given)."""
    sc = bpy.context.scene
    _bake_animations()
    sc.frame_set(1)
    meshes = [o for o in sc.objects if o.type == "MESH"]
    animated = [o for o in meshes if o.animation_data and o.animation_data.action]
    static = [o for o in meshes if o not in animated]
    objs = list(animated)
    if static:
        for o in sc.objects:
            o.select_set(False)
        for o in static:
            o.select_set(True)
        bpy.context.view_layer.objects.active = static[0]
        bpy.ops.object.convert(target="MESH")
        bpy.ops.object.join()
        body = bpy.context.active_object
        body.name = "model"
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        objs.append(body)
    if not objs:
        raise RuntimeError("finish(): no mesh objects")
    bpy.context.view_layer.update()
    lo, hi = _world_bounds(objs)
    center = (lo + hi) / 2
    root = bpy.data.objects.new("model_root", None)
    sc.collection.objects.link(root)
    for o in objs:
        mw = o.matrix_world.copy()
        o.parent = root
        o.matrix_parent_inverse = root.matrix_world.inverted()
        o.matrix_world = mw
    root.location = (-center.x, -center.y, -center.z)
    if height:
        k = height / max(hi.z - lo.z, 1e-6)
        root.scale = (k, k, k)
        root.location = (-center.x * k, -center.y * k, -center.z * k)
    return root
