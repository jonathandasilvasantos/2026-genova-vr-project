"""Deterministic layout review of a scene, no GPU and no LLM.

It replays the player's geometry: where every node is at each step (parents, orbits, paths, looping
motion, show/hide/set actions), where the camera is and where it looks, then projects labels, text panels,
objects and the caption into view angles and reports problems a human would see in the headset:
labels on top of each other, the caption covering the object being explained, the object too small or too
big, text pushed out of view. `reframe()` fixes camera distance/height automatically; the rest is returned
as messages for the scene designer to fix.

The constants below MUST match the player (vr_player.cc / TextureFactory.java).
Nothing here is subject-specific.
"""
import math

# ---- player constants -------------------------------------------------------------------------
FOV_H, FOV_V = 40.0, 35.0       # half-angles (degrees) of the comfortably visible area
EYE_START = (0.0, 1.6, 0.0)
LABEL_LIFT = 0.18               # label height above its node, plus scale * 1.35
LABEL_HALF_H = 0.14             # half height of the auto-label quad (metres)
CAPTION_Y, CAPTION_Z, CAPTION_HALF_W = -0.50, -1.4, 0.50   # head-locked caption quad
CAPTION_PX_W, CAPTION_FONT = 1100, 44
LABEL_PX_W, LABEL_FONT = 640, 64
PANEL_MAX_HALF_H = 0.35         # text panels (quad with label) are clamped to this scale
TARGET_DIAMETER = (16.0, 42.0)  # focused object should span this many degrees
EST_SECONDS_PER_CHAR = 0.065
EYE_RANGE = (1.0, 2.4)          # natural eye height (m); never put the viewer underground or in the sky
MOVE_BLINK = 0.75               # a viewpoint change smaller than this is ignored by the player
MAX_VIEWPOINTS = 3              # per scene: every change is a visible blink
MAX_SIDE_ANGLE = 50.0           # a focused object further than this from the scene's forward direction needs a head turn


def _text_box(text, px_w, font, bold_factor=0.55):
    """Estimated (width_px, height_px) of wrapped text in the player's text texture."""
    pad = font * 0.5
    per_line = max(6, int((px_w - 2 * pad) / (font * bold_factor)))
    lines = max(1, math.ceil(len(text) / per_line))
    width = min(px_w, len(text) * font * bold_factor + 2 * pad)
    return width, lines * font * 1.2 + 2 * pad


def _lerp(a, b, u):
    return [a[i] + (b[i] - a[i]) * u for i in range(len(a))]


class State:
    """Mutable view of a scene's nodes as the guided steps change them."""

    def __init__(self, scene):
        self.nodes = {}
        self.order = []
        self.children = {}
        for n in scene["nodes"]:
            self.nodes[n["id"]] = {"n": n, "pos": list(n.get("pos", [0, 0, 0])), "scale": n.get("scale", 1.0),
                                   "opacity": 0.0 if n.get("visible") is False else 1.0}
            self.order.append(n["id"])
            if n.get("parent"):
                self.children.setdefault(n["parent"], []).append(n["id"])

    def extent(self, nid, t=0.0, depth=0):
        """Radius (metres) of a node including its visible solid descendants: a group root such as an empty
        sphere that holds a whole exhibit is as big as the exhibit."""
        s = self.nodes[nid]
        n = s["n"]
        r = node_radius(n, s["scale"]) if n["kind"] not in ("disc", "quad", "emitter", "light", "effect") else 0.0
        if depth > 6:
            return r
        w0, _ = self.world(nid, t)
        for c in self.children.get(nid, []):
            cn = self.nodes[c]["n"]
            if cn["kind"] in ("disc", "quad", "emitter", "light", "effect") or self.nodes[c]["opacity"] < 0.3:
                continue
            w1, _ = self.world(c, t)
            r = max(r, math.dist(w0, w1) + self.extent(c, t, depth + 1))
        return r

    def apply(self, step):
        for key in ("show", "hide"):
            for i in step.get(key, []):
                if i in self.nodes:
                    self.nodes[i]["opacity"] = 1.0 if key == "show" else 0.0
        for op in step.get("set", []):
            s = self.nodes.get(op["node"])
            if not s:
                continue
            if "position" in op:
                s["pos"] = list(op["position"])
            if "scale" in op:
                s["scale"] = op["scale"]
            if "opacity" in op:
                s["opacity"] = op["opacity"]

    def world(self, node_id, t):
        """World position and effective opacity of a node at time t (same maths as the player)."""
        s = self.nodes[node_id]
        n = s["n"]
        base = [0.0, 0.0, 0.0]
        parent_op = 1.0
        if n.get("parent") and n["parent"] in self.nodes:
            base, parent_op = self.world(n["parent"], t)
        px, py, pz = s["pos"]
        ox = oz = 0.0
        orbit = n.get("orbit")
        if orbit and orbit.get("radius", 0) > 0:
            a = orbit.get("phase", 0) + orbit.get("speed", 0) * t
            ox, oz = orbit["radius"] * math.cos(a), orbit["radius"] * math.sin(a)
        off = [0.0, 0.0, 0.0]
        op = s["opacity"] * parent_op
        for tr in n.get("animate", []):
            u = _phase(t - tr.get("delay", 0), tr.get("seconds", 2), tr.get("loop", "pingpong"))
            if tr["prop"] == "position":
                fr, to = _vec(tr.get("from", 0)), _vec(tr["to"])
                v = _lerp(fr, to, u)
                off = [off[i] + v[i] for i in range(3)]
            elif tr["prop"] == "opacity":
                op *= _lerp([tr.get("from", 1)], [tr["to"]], u)[0]
        path = n.get("path")
        if path and len(path.get("points", [])) >= 2:
            pts = path["points"]
            loop = path.get("loop", "loop")
            cnt = len(pts)
            u = _phase(t + path.get("phase", 0) * path.get("seconds", 6), path.get("seconds", 6), loop)
            u *= cnt if loop == "loop" else cnt - 1
            i0 = min(int(u), cnt - 1)
            i1 = (i0 + 1) % cnt if loop == "loop" else min(i0 + 1, cnt - 1)
            px, py, pz = _lerp(pts[i0], pts[i1], u - math.floor(u))
        return [base[0] + px + ox + off[0], base[1] + py + off[1], base[2] + pz + oz + off[2]], op


def _vec(v):
    return [v, v, v] if isinstance(v, (int, float)) else list(v)


def _phase(el, seconds, loop):
    if el < 0:
        return 0.0
    u = el / max(seconds, 0.05)
    if loop == "once":
        return min(1.0, u)
    if loop == "loop":
        return u - math.floor(u)
    m = u % 2.0
    return m if m <= 1 else 2 - m


class Camera:
    """The viewer looks along the scene's FIXED forward direction (the world never turns under their head)."""

    def __init__(self, eye, yaw):
        self.eye = eye
        self.yaw = yaw

    def project(self, p):
        """Angles (degrees) right/up of the view centre, and distance. None if behind the viewer."""
        x, y, z = p[0] - self.eye[0], p[1] - self.eye[1], p[2] - self.eye[2]
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        xr, zr = x * c - z * s, x * s + z * c       # rotate the world so the focus is straight ahead (-z)
        fwd = -zr
        if fwd < 0.05:
            return None
        return math.degrees(math.atan2(xr, fwd)), math.degrees(math.atan2(y, math.hypot(xr, fwd))), math.sqrt(x * x + y * y + z * z)


def _rect(ax, ay, half_w_m, half_h_m, dist):
    hw = math.degrees(math.atan2(half_w_m, dist))
    hh = math.degrees(math.atan2(half_h_m, dist))
    return (ax - hw, ay - hh, ax + hw, ay + hh)


def _overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / max(1e-6, min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])))


def caption_rect(text):
    wpx, hpx = _text_box(text, CAPTION_PX_W, CAPTION_FONT)
    half_h = min(0.30, CAPTION_HALF_W * hpx / CAPTION_PX_W)
    d = -CAPTION_Z
    return (math.degrees(math.atan2(-CAPTION_HALF_W, d)), math.degrees(math.atan2(CAPTION_Y - half_h, d)),
            math.degrees(math.atan2(CAPTION_HALF_W, d)), math.degrees(math.atan2(CAPTION_Y + half_h, d)))


def node_radius(n, scale):
    st = n.get("stretch") or [1, 1, 1]
    return max(0.02, scale * max(st))


def step_camera(step, state, t, prev_eye):
    cam = step.get("camera") or {}
    focus_id = cam.get("focus")
    if cam.get("follow") and cam["follow"] in state.nodes:
        fw, _ = state.world(cam["follow"], t)
        off = cam.get("pos", [0, 0, 3])
        eye = [fw[0] + off[0], fw[1] + off[1], fw[2] + off[2]]
    elif cam.get("pos"):
        eye = list(cam["pos"])
    else:
        eye = list(prev_eye)
    focus_w = state.world(focus_id, t)[0] if focus_id in state.nodes else [eye[0], eye[1], eye[2] - 5]
    return eye, focus_id, focus_w


def review_step(scene, state, step, k, prev_eye, base_yaw, text=None):
    """Problems for one step (list of strings), sampled at the start, middle and end of the step."""
    text = text or step["text"]
    dur = step.get("duration") or max(2.5, len(text) * EST_SECONDS_PER_CHAR)
    probs = []
    cap = caption_rect(text)
    hl = step.get("highlight") or (step.get("camera") or {}).get("focus")
    seen = set()
    for t in (0.3, dur * 0.5, dur * 0.95):
        eye, focus_id, focus_w = step_camera(step, state, t, prev_eye)
        cam = Camera(eye, base_yaw)
        boxes = []
        discs = []   # (id, projected rect) of solid objects, to catch labels lying on other objects
        for nid in state.order:
            n = state.nodes[nid]["n"]
            w, op = state.world(nid, t)
            if op < 0.3:
                continue
            pr = cam.project(w)
            if pr is None:
                continue
            ax, ay, dist = pr
            scale = state.nodes[nid]["scale"]
            rad = max(node_radius(n, scale), state.extent(nid, t) if nid == focus_id else 0.0)
            if n["kind"] not in ("quad", "disc", "emitter", "light", "effect", "sky") and dist > 0.3:
                d_ang = math.degrees(math.atan2(node_radius(n, scale), dist))
                if 1.5 < d_ang < 25:   # bigger ones are backdrops/frames that everything sits in front of
                    discs.append((nid, (ax - d_ang, ay - d_ang, ax + d_ang, ay + d_ang)))
            if n.get("label"):
                if n["kind"] == "quad" and not n.get("texture"):   # text panel
                    wpx, hpx = _text_box(n["label"], LABEL_PX_W, LABEL_FONT)
                    hh = min(scale, PANEL_MAX_HALF_H)
                    boxes.append((nid, "panel", _rect(ax, ay, hh * wpx / hpx, hh, dist), n["label"]))
                elif nid == hl:   # auto-labels are shown for the highlighted/looked-at node only
                    wpx, hpx = _text_box(n["label"], LABEL_PX_W, LABEL_FONT)
                    ly = w[1] + n.get("scale", 1.0) * 1.35 + LABEL_LIFT
                    pl = cam.project([w[0], ly, w[2]]) or (ax, ay, dist)
                    boxes.append((nid, "label", _rect(pl[0], pl[1], LABEL_HALF_H * wpx / hpx, LABEL_HALF_H, pl[2]), n["label"]))
            if nid == focus_id:
                diam = 2 * math.degrees(math.atan2(rad, dist))
                if diam < TARGET_DIAMETER[0] * 0.6:
                    seen.add(f"step {k}: the focused object '{nid}' is too small in view ({diam:.0f} degrees); move the camera closer")
                if diam > 80:
                    seen.add(f"step {k}: the focused object '{nid}' fills the whole view ({diam:.0f} degrees); move the camera back")
                if abs(ay) > 32:
                    seen.add(f"step {k}: the focused object '{nid}' is {'above' if ay > 0 else 'below'} the line of sight by {abs(ay):.0f} degrees")
                if abs(ax) > MAX_SIDE_ANGLE:
                    seen.add(f"step {k}: the focused object '{nid}' is {abs(ax):.0f} degrees to the side of the scene's forward direction; keep the content in an arc within +-{MAX_SIDE_ANGLE:.0f} degrees ahead")
                disc = (ax - diam / 2, ay - diam / 2, ax + diam / 2, ay + diam / 2)
                if diam > 6 and (disc[3] > FOV_V or disc[1] < -FOV_V or disc[2] > FOV_H or disc[0] < -FOV_H):
                    seen.add(f"step {k}: the focused object '{nid}' is cut off by the edge of the view; move the camera back or aim it at the object so it sits inside the view")
                if _overlap(disc, cap) > 0.45 and diam > 6:
                    seen.add(f"step {k}: the caption covers the focused object '{nid}'; raise it in view (camera lower) or move it")
        def related(a, b):
            def chain(x):
                out = set()
                while x and x not in out:
                    out.add(x)
                    x = state.nodes[x]["n"].get("parent") if x in state.nodes else None
                return out
            return a in chain(b) or b in chain(a)
        for nid, kind, r, label in boxes:
            if kind != "label":
                continue
            for did, drect in discs:
                if did != nid and not related(did, nid) and _overlap(r, drect) > 0.35:
                    seen.add(f"step {k}: the label '{label}' lies on top of the object '{did}'; move the labelled object or the other one apart, or make the label's object stand clear (labels sit above their object)")
                    break
        for i in range(len(boxes)):
            nid, kind, r, label = boxes[i]
            cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
            if abs(cx) > FOV_H + 8 or abs(cy) > FOV_V + 8:
                continue   # not in the visible area, nobody reads it
            if abs(cx) > FOV_H or abs(cy) > FOV_V or r[0] < -FOV_H - 4 or r[2] > FOV_H + 4:
                seen.add(f"step {k}: the {kind} '{label}' is cut off at the edge of the view")
            if _overlap(r, cap) > 0.3 and (kind == "panel" or nid == hl):
                seen.add(f"step {k}: the caption covers the {kind} '{label}'")
            for j in range(i + 1, len(boxes)):
                if _overlap(r, boxes[j][2]) > 0.2:
                    seen.add(f"step {k}: the {kind} '{label}' overlaps '{boxes[j][3]}'")
    probs.extend(sorted(seen))
    return probs


def base_heading(scene, state):
    """Yaw (radians) of the scene's forward direction: from the first viewpoint towards the first focus."""
    first = scene["steps"][0]
    eye, focus_id, fw = step_camera(first, state, 0.3, list(EYE_START))
    dx, dz = fw[0] - eye[0], fw[2] - eye[2]
    return math.atan2(dx, -dz) if (abs(dx) + abs(dz)) > 1e-6 else 0.0


def review_scene(scene):
    """All layout problems of a scene, replaying the steps in order."""
    state = State(scene)
    yaw = base_heading(scene, state)
    eye = list(EYE_START)
    out = []
    changes = 0
    for k, step in enumerate(scene["steps"]):
        state.apply(step)
        out.extend(review_step(scene, state, step, k, eye, yaw))
        new_eye, _, _ = step_camera(step, state, 0.3, eye)
        if k > 0 and math.dist(new_eye, eye) > MOVE_BLINK:
            changes += 1
        if (step.get("camera") or {}).get("follow"):
            out.append(f"step {k}: camera.follow is not allowed (the viewer must never be carried); use a fixed viewpoint")
        eye = new_eye
    if changes > MAX_VIEWPOINTS:
        out.append(f"the scene changes the viewpoint {changes} times; every change is a visible blink, keep it to at most {MAX_VIEWPOINTS} (prefer turning the head and highlights)")
    n_pan = sum(1 for n in scene["nodes"] if n["kind"] == "quad" and n.get("label") and not n.get("texture"))
    if n_pan > 3:
        out.append(f"the scene has {n_pan} text panels; keep at most 3 and use the caption for explanations")
    return out


def _acceptable(eye, yaw, fw, rad):
    cam = Camera(eye, yaw)
    pr = cam.project(fw)
    if pr is None:
        return False
    ax, ay, dist = pr
    diam = 2 * math.degrees(math.atan2(rad, max(dist, 0.05)))
    if not (10.0 <= diam <= 60.0 and abs(ax) <= MAX_SIDE_ANGLE * 0.9 and -14.0 <= ay <= 28.0):
        return False
    # the whole object must sit inside the visible area, not just its centre
    return ay + diam / 2 <= FOV_V - 1 and ay - diam / 2 >= -FOV_V and abs(ax) + diam / 2 <= FOV_H


def reframe(scene):
    """Comfort-first framing. The viewer is never carried: the scene has one forward direction, the eye stays at a
    natural height, and the viewpoint changes only when the focused object cannot be seen well from the current
    one (then it moves along that forward axis so the object ends up straight ahead, a bit above the caption).
    `follow` is converted to a fixed viewpoint. Returns the number of steps changed."""
    state = State(scene)
    yaw = base_heading(scene, state)
    heading = (math.sin(yaw), -math.cos(yaw))   # unit vector (x, z) of the forward direction
    eye = list(EYE_START)
    changed = 0
    for k, step in enumerate(scene["steps"]):
        state.apply(step)
        cam = step.get("camera") or {}
        focus = cam.get("focus")
        t = 0.3
        cur, _, _ = step_camera(step, state, t, eye)
        want = list(cur)
        if k == 0 and cam.get("pos"):
            want = list(cam["pos"])
        if focus in state.nodes:
            fw, _ = state.world(focus, t)
            rad = max(node_radius(state.nodes[focus]["n"], state.nodes[focus]["scale"]), state.extent(focus, t))
            if k > 0:
                if _acceptable(eye, yaw, fw, rad):
                    want = list(eye)          # stay: no blink
                else:
                    half = math.radians((TARGET_DIAMETER[0] + TARGET_DIAMETER[1]) / 4)
                    d = max(1.2, min(14.0, rad / math.tan(half)))
                    ey = max(EYE_RANGE[0], min(EYE_RANGE[1], fw[1] - d * math.tan(math.radians(8))))
                    want = [fw[0] - heading[0] * d, ey, fw[2] - heading[1] * d]
            want[1] = max(EYE_RANGE[0], min(EYE_RANGE[1], want[1]))
        elif k > 0:
            want = list(eye)
        if k > 0 and math.dist(want, eye) <= MOVE_BLINK:
            want = list(eye)
        new_pos = [round(v, 2) for v in want]
        if cam.get("pos") != new_pos or cam.get("follow"):
            changed += 1
        cam["pos"] = new_pos
        cam.pop("follow", None)
        step["camera"] = cam
        eye = want
    return changed
