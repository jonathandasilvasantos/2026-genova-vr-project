"""Runs INSIDE Blender (headless): `blender -b --python blender_bridge.py -- --port 9876`.

Speaks the same JSON-over-TCP protocol as the blender-mcp addon (commands
`get_scene_info`, `execute_code`, `get_viewport_screenshot`) so any blender-mcp client can drive
it, plus two extras for the pipeline: `export_glb` and `reset_scene`. Headless, so screenshots
are real renders (EEVEE/Workbench) instead of viewport grabs.
"""
import io
import json
import math
import socket
import sys
import traceback
from contextlib import redirect_stdout

import bpy
from mathutils import Vector

PORT = 9876
if "--port" in sys.argv:
    PORT = int(sys.argv[sys.argv.index("--port") + 1])


def animation_info():
    n = [o.name for o in bpy.context.scene.objects if o.animation_data and o.animation_data.action]
    sc = bpy.context.scene
    return {"animated_objects": n, "frames": [sc.frame_start, sc.frame_end], "fps": sc.render.fps} if n else None


def scene_info():
    objs = []
    for o in bpy.context.scene.objects:
        info = {"name": o.name, "type": o.type, "location": [round(v, 3) for v in o.location]}
        if o.type == "MESH":
            info["vertices"] = len(o.data.vertices)
            info["triangles"] = sum(len(p.vertices) - 2 for p in o.data.polygons)
            info["dimensions"] = [round(v, 3) for v in o.dimensions]
        objs.append(info)
    return {"name": bpy.context.scene.name, "object_count": len(objs), "objects": objs[:50], "animation": animation_info()}


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return {"ok": True}


def bounds():
    pts = []
    for o in bpy.context.scene.objects:
        if o.type == "MESH":
            pts += [o.matrix_world @ Vector(c) for c in o.bound_box]
    if not pts:
        return Vector((0, 0, 0)), 1.0
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (lo + hi) / 2, max((hi - lo).length / 2, 1e-3)


RENDER_DEVICE = "eevee/workbench"


def render_views(filepath, size=384, views=("front", "side", "top", "iso", "back", "closeup")):
    global RENDER_DEVICE
    """Render a contact sheet of the scene from several angles; returns the PNG path."""
    scn = bpy.context.scene
    center, radius = bounds()
    import os
    if os.environ.get("BLENDER_RENDER_ENGINE") == "CYCLES_GPU":
        # Server mode (Cloud Run L4): path-traced previews on the GPU, no display needed.
        scn.render.engine = "CYCLES"
        scn.cycles.samples = 24
        scn.cycles.use_denoising = False
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for kind in ("OPTIX", "CUDA"):
            try:
                prefs.compute_device_type = kind
                prefs.get_devices()
                gpus = [d for d in prefs.devices if d.type == kind]
                if gpus:
                    for d in prefs.devices:
                        d.use = d.type == kind
                    scn.cycles.device = "GPU"
                    RENDER_DEVICE = f"cycles-{kind.lower()}-gpu"
                    break
            except Exception:  # noqa: BLE001 - try the next backend
                continue
        else:
            scn.cycles.device = "CPU"
            scn.cycles.samples = 12
            RENDER_DEVICE = "cycles-cpu"
    else:
        scn.render.engine = "BLENDER_EEVEE" if "BLENDER_EEVEE" in {e.identifier for e in
            bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items} else "BLENDER_WORKBENCH"
    scn.render.resolution_x = scn.render.resolution_y = size
    scn.render.film_transparent = False
    world = bpy.data.worlds.new("w") if not scn.world else scn.world
    scn.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.12, 0.13, 0.16, 1)
        bg.inputs[1].default_value = 1.0
    # simple 3-point light rig
    for name, loc, energy in (("key", (4, -4, 5), 800), ("fill", (-5, -2, 2), 300), ("rim", (0, 5, 3), 400)):
        ld = bpy.data.lights.new(name, "SUN" if name != "key" else "SUN")
        ld.energy = {"key": 3.0, "fill": 1.2, "rim": 1.5}[name]
        lo = bpy.data.objects.new(name, ld)
        lo.rotation_euler = (math.radians(50), 0, math.radians({"key": 35, "fill": -70, "rim": 180}[name]))
        scn.collection.objects.link(lo)
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 50
    cam = bpy.data.objects.new("cam", cam_data)
    scn.collection.objects.link(cam)
    scn.camera = cam
    dist = radius * 3.2
    dirs = {"front": Vector((0, -1, 0.15)), "side": Vector((1, 0, 0.15)), "top": Vector((0.001, -0.05, 1)),
            "iso": Vector((1, -1, 0.8)), "back": Vector((0, 1, 0.2)), "closeup": Vector((0.8, -1, 0.45))}
    paths = []
    anim = animation_info()
    if anim:
        views = tuple(views) + ("motion1", "motion2")
        dirs["motion1"] = dirs["motion2"] = dirs["iso"]
    for v in views:
        if v.startswith("motion"):
            f0, f1 = anim["frames"]
            scn.frame_set(int(f0 + (f1 - f0) * (0.3 if v == "motion1" else 0.65)))
        elif anim:
            scn.frame_set(anim["frames"][0])
        d = dirs[v].normalized()
        cam.location = center + d * (dist * 0.55 if v == "closeup" else dist)
        direction = center - cam.location
        cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        p = f"{filepath}.{v}.png"
        scn.render.filepath = p
        bpy.ops.render.render(write_still=True)
        paths.append(p)
    # contact sheet via pixel copy: 3 columns, as many rows as needed
    imgs = [bpy.data.images.load(p) for p in paths]
    w, h = imgs[0].size
    cols = 3 if len(imgs) > 4 else 2
    rows = (len(imgs) + cols - 1) // cols
    sheet = bpy.data.images.new("sheet", w * cols, h * rows)
    px = [0.0] * (w * cols * h * rows * 4)
    for k, im in enumerate(imgs):
        data = list(im.pixels)
        ox, oy = (k % cols) * w, (rows - 1 - k // cols) * h
        for y in range(h):
            row = data[y * w * 4:(y + 1) * w * 4]
            start = ((oy + y) * w * cols + ox) * 4
            px[start:start + w * 4] = row
    sheet.pixels = px
    sheet.filepath_raw = filepath
    sheet.file_format = "PNG"
    sheet.save()
    import os
    for pth in paths:
        try:
            os.remove(pth)
        except OSError:
            pass
    for o in (cam, *[o for o in scn.objects if o.name in ("key", "fill", "rim")]):
        bpy.data.objects.remove(o, do_unlink=True)
    return filepath


def cleanup():
    """Deterministic tidy-up before export: weld, fix normals, drop loose bits, angle-based smoothing."""
    import bmesh
    stats = {"welded": 0, "loose_removed": 0, "objects": 0}
    for o in [o for o in bpy.context.scene.objects if o.type == "MESH"]:
        bm = bmesh.new()
        bm.from_mesh(o.data)
        n0 = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
        stats["welded"] += n0 - len(bm.verts)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        loose = [v for v in bm.verts if not v.link_faces]
        stats["loose_removed"] += len(loose)
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bm.to_mesh(o.data)
        bm.free()
        stats["objects"] += 1
        try:
            bpy.ops.object.select_all(action="DESELECT")
            o.select_set(True)
            bpy.context.view_layer.objects.active = o
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(40))
        except Exception:  # noqa: BLE001 - operator name differs between Blender versions
            pass
    for m in list(bpy.data.materials):
        if m.users == 0:
            bpy.data.materials.remove(m)
    return stats


def export_glb(filepath):
    try:
        stats = cleanup()
        print("CLEANUP", stats, flush=True)
    except Exception:  # noqa: BLE001 - never block an export on tidy-up
        traceback.print_exc()
    bpy.ops.object.select_all(action="SELECT")
    kw = dict(filepath=filepath, export_format="GLB", export_apply=True, export_yup=True,
              export_materials="EXPORT", export_cameras=False, export_lights=False, use_selection=True)
    anim = animation_info()
    if anim:
        kw.update(export_animations=True, export_animation_mode="ACTIVE_ACTIONS", export_force_sampling=True,
                  export_frame_range=True)
    else:
        kw.update(export_animations=False)
    bpy.context.scene.frame_set(bpy.context.scene.frame_start)
    for attempt in range(4):  # drop options a given Blender version does not know
        try:
            bpy.ops.export_scene.gltf(**kw)
            break
        except TypeError as e:
            bad = str(e).split('"')[1] if '"' in str(e) else None
            if not bad or bad not in kw:
                raise
            kw.pop(bad)
    import os
    return {"path": filepath, "bytes": os.path.getsize(filepath)}


def handle(cmd):
    t, p = cmd.get("type"), cmd.get("params", {})
    if t == "get_scene_info":
        return scene_info()
    if t == "execute_code":
        buf = io.StringIO()
        ns = {"bpy": bpy, "math": math, "Vector": Vector}
        with redirect_stdout(buf):
            exec(p.get("code", ""), ns)
        return {"executed": True, "result": buf.getvalue()}
    if t == "get_viewport_screenshot":
        out = render_views(p.get("filepath", "/tmp/shot.png"), size=int(p.get("max_size", 384)))
        return {"success": True, "filepath": out, "device": RENDER_DEVICE}
    if t == "cleanup":
        return cleanup()
    if t == "export_glb":
        return export_glb(p["filepath"])
    if t == "reset_scene":
        return reset_scene()
    if t == "shutdown":
        raise SystemExit
    raise ValueError(f"unknown command {t}")


def serve():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(1)
    print(f"BRIDGE_READY {PORT}", flush=True)
    while True:
        conn, _ = srv.accept()
        buf = b""
        with conn:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                try:
                    cmd = json.loads(buf.decode())
                except json.JSONDecodeError:
                    continue
                buf = b""
                try:
                    res = {"status": "success", "result": handle(cmd)}
                except SystemExit:
                    conn.sendall(json.dumps({"status": "success", "result": "bye"}).encode())
                    return
                except Exception as e:  # noqa: BLE001 - report every failure to the client
                    res = {"status": "error", "message": f"{type(e).__name__}: {e}",
                           "traceback": traceback.format_exc()[-1500:]}
                conn.sendall(json.dumps(res).encode())
                break


serve()
