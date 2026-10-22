package com.google.cardboard;

import android.media.MediaPlayer;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import java.io.File;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Generic interpreter for a Genova VR bundle (manifest.json + assets). It knows nothing about any
 * subject: it builds nodes from the scene description and walks the guided steps.
 */
final class ExperiencePlayer {
  private static final String TAG = "ExperiencePlayer";
  static final int STRIDE = VisualEffects.STRIDE;

  /** Bridge to the native renderer. */
  interface Renderer {
    void setScene(float[] nodes, String[] textureKeys);
    void moveCamera(float x, float y, float z, int focusNode, float seconds, int followNode);
    void setHighlight(int node);
    void setSubtitle(String text);
    void setGazeEnabled(boolean enabled);
    void setAnimations(float[] data);
    default void setFog(float[] data) {}
    default void burst(int node) {}
    void tweenNode(int node, int prop, float x, float y, float z, float seconds);
  }

  private final File dir;
  private final Renderer r;
  private final Handler handler = new Handler(Looper.getMainLooper());
  private JSONObject manifest;
  private MediaPlayer media;
  private MediaPlayer ambience;
  private final MusicDeck music = new MusicDeck(3500);
  private int sceneIndex = -1;
  private final Map<String, Integer> nodeIndex = new HashMap<>();
  private JSONObject scene;
  private int stepIndex;
  private boolean finished;
  private JSONObject quiz;
  private int[] optionCorrect;
  private float[] animData = new float[0];
  private float[] fogData = new float[7];
  private final List<JSONObject> appliedSets = new ArrayList<>();
  private boolean menuOpen;
  private boolean inQuiz;
  private boolean quizAcceptingAnswers;
  private Runnable onExit;

  void setOnExit(Runnable r) {
    onExit = r;
  }

  ExperiencePlayer(File bundleDir, Renderer renderer) {
    this.dir = bundleDir;
    this.r = renderer;
  }

  boolean load() {
    try {
      manifest = new JSONObject(Files.read(new File(dir, "manifest.json")));
      return manifest.getJSONArray("scenes").length() > 0;
    } catch (Exception e) {
      Log.e(TAG, "cannot load bundle from " + dir, e);
      return false;
    }
  }

  File dir() {
    return dir;
  }

  void start() {
    start(0);
  }

  /** Debug/preview: begin at a given scene index. */
  void start(int scene) {
    finished = false;
    menuOpen = false;
    startScene(Math.max(0, scene));
  }

  void stop() {
    handler.removeCallbacksAndMessages(null);
    if (media != null) {
      media.release();
      media = null;
    }
    stopAmbience();
    music.release();
  }

  /** Applies the scene's music choice: a track id, "none" to fade out, or nothing to keep playing. */
  private void applyMusic(JSONObject sc) {
    String id = sc.optString("music", "");
    if (id.isEmpty()) return;
    if (id.equals("none")) {
      music.stop();
      return;
    }
    JSONArray tracks = manifest.optJSONArray("tracks");
    for (int i = 0; tracks != null && i < tracks.length(); i++) {
      JSONObject t = tracks.optJSONObject(i);
      if (t != null && id.equals(t.optString("id"))) {
        music.play(new File(dir, t.optString("audio", "")), (float) t.optDouble("volume", 0.16));
        return;
      }
    }
  }

  private void stopAmbience() {
    if (ambience != null) {
      ambience.release();
      ambience = null;
    }
  }

  /** Looping background bed, kept quiet so the narration always dominates. */
  private void startAmbience(JSONObject amb) {
    stopAmbience();
    if (amb == null || amb.optString("audio", "").isEmpty()) return;
    File f = new File(dir, amb.optString("audio"));
    if (!f.exists()) return;
    try {
      float vol = (float) amb.optDouble("volume", 0.18);
      ambience = new MediaPlayer();
      ambience.setDataSource(f.getAbsolutePath());
      ambience.setLooping(true);
      ambience.setVolume(vol, vol);
      ambience.prepare();
      ambience.start();
    } catch (Exception e) {
      Log.e(TAG, "ambience failed: " + f, e);
      stopAmbience();
    }
  }

  /** Cues fire when the narrator says their phrase: move the highlight and run show/hide/set actions. */
  private void scheduleCues(JSONObject step) {
    JSONArray cues = step.optJSONArray("cues");
    for (int i = 0; cues != null && i < cues.length(); i++) {
      final JSONObject cue = cues.optJSONObject(i);
      if (cue == null) continue;
      handler.postDelayed(
          () -> {
            if (menuOpen) return;
            Log.i(TAG, "cue '" + cue.optString("phrase") + "' fired (planned at " + cue.optDouble("at", 0) + "s)");
            Integer hl = nodeIndex.get(cue.optString("highlight", ""));
            if (hl != null) r.setHighlight(hl);
            applyActions(cue, true, -1f);
          },
          (long) (cue.optDouble("at", 0) * 1000));
    }
  }

  /** One-shot sound effect, fired shortly after a step starts. */
  private void playSfx(JSONObject sfx) {
    if (sfx == null || sfx.optString("audio", "").isEmpty()) return;
    final File f = new File(dir, sfx.optString("audio"));
    if (!f.exists()) return;
    final float vol = (float) sfx.optDouble("volume", 0.5);
    handler.postDelayed(
        () -> {
          try {
            final MediaPlayer p = new MediaPlayer();
            p.setDataSource(f.getAbsolutePath());
            p.setVolume(vol, vol);
            p.setOnCompletionListener(MediaPlayer::release);
            p.prepare();
            p.start();
          } catch (Exception e) {
            Log.e(TAG, "sfx failed: " + f, e);
          }
        },
        (long) (sfx.optDouble("delay", 0.5) * 1000));
  }

  // ---- scenes -----------------------------------------------------------
  private void startScene(int index) {
    inQuiz = false;
    quizAcceptingAnswers = false;
    quiz = null;
    r.setGazeEnabled(false);
    try {
      JSONArray scenes = manifest.getJSONArray("scenes");
      if (index >= scenes.length()) {
        finished = true;
        stopAmbience();
        music.stop();
        r.setSubtitle("The end. Thank you for exploring!");
        r.setGazeEnabled(false);
        return;
      }
      sceneIndex = index;
      scene = scenes.getJSONObject(index);
      appliedSets.clear();
      buildScene(scene, null);
      applyMusic(scene);
      startAmbience(scene.optJSONObject("ambience"));
      stepIndex = 0;
      handler.postDelayed(this::runStep, 800);
    } catch (JSONException e) {
      Log.e(TAG, "bad scene", e);
    }
  }

  private static float f(JSONArray a, int i, float def) {
    return a != null && i < a.length() ? (float) a.optDouble(i, def) : def;
  }

  private void buildScene(JSONObject sc, List<float[]> extra) throws JSONException {
    List<float[]> nodes = new ArrayList<>();
    List<String> texKeys = new ArrayList<>();
    List<Float> anim = new ArrayList<>();
    nodeIndex.clear();

    JSONObject sky = sc.optJSONObject("sky");
    float[] skyNode = newNode(3);
    skyNode[4] = 1;
    float[] skyColor = {0.02f, 0.02f, 0.06f, 1};
    if (sky != null && sky.optJSONArray("color") != null) {
      JSONArray c = sky.getJSONArray("color");
      for (int i = 0; i < 3; i++) skyColor[i] = f(c, i, 0);
    }
    System.arraycopy(skyColor, 0, skyNode, 6, 4);
    if (sky != null && sky.has("texture")) {
      skyNode[18] = texIndex(texKeys, "file:" + sky.getString("texture"));
      skyNode[6] = skyNode[7] = skyNode[8] = 1;
    }
    nodes.add(skyNode);

    JSONArray arr = VisualEffects.expand(sc.optJSONArray("nodes"));
    for (int i = 0; arr != null && i < arr.length(); i++) {
      JSONObject n = arr.getJSONObject(i);
      String kind = n.optString("kind", "sphere");
      int kindCode = kind.equals("disc") ? 1 : kind.equals("quad") ? 2 : kind.equals("box") ? 4
          : kind.equals("cylinder") ? 5 : kind.equals("cone") ? 6 : kind.equals("torus") ? 7 : kind.equals("model") ? 8
          : kind.equals("emitter") ? VisualEffects.EMITTER : kind.equals("light") ? VisualEffects.LIGHT
          : kind.equals("effect") || n.optBoolean("effect_group", false) ? VisualEffects.GROUP : 0;
      float[] d = newNode(kindCode);
      JSONArray pos = n.optJSONArray("pos");
      d[1] = f(pos, 0, 0);
      d[2] = f(pos, 1, 0);
      d[3] = f(pos, 2, 0);
      d[4] = (float) n.optDouble("scale", 1);
      JSONArray col = n.optJSONArray("color");
      d[6] = f(col, 0, 1);
      d[7] = f(col, 1, 1);
      d[8] = f(col, 2, 1);
      d[9] = f(col, 3, 1);
      d[10] = n.optBoolean("lit", false) ? 1 : 0;
      String parent = n.optString("parent", "");
      d[11] = parent.isEmpty() || !nodeIndex.containsKey(parent) ? -1 : nodeIndex.get(parent);
      JSONObject orbit = n.optJSONObject("orbit");
      if (orbit != null) {
        d[12] = (float) orbit.optDouble("radius", 0);
        d[13] = (float) orbit.optDouble("speed", 0);
        d[14] = (float) orbit.optDouble("phase", 0);
      }
      d[15] = (float) n.optDouble("spin", 0);
      d[16] = (float) n.optDouble("inner", 0);
      d[17] = n.optBoolean("pickable", false) ? i : -1;
      if (kindCode == 8) d[18] = texIndex(texKeys, "model:" + n.getString("model"));
      else if (n.has("texture")) d[18] = texIndex(texKeys, "file:" + n.getString("texture"));
      d[19] = (n.optBoolean("billboard", false) ? 1 : 0) | (n.optBoolean("light", false) ? 4 : 0);
      d[20] = (float) n.optDouble("tilt", 0);
      JSONArray rot = n.optJSONArray("rot");
      d[21] = f(rot, 0, 0);
      d[22] = f(rot, 1, 0);
      d[23] = f(rot, 2, 0);
      JSONArray st = n.optJSONArray("stretch");
      d[24] = f(st, 0, 1);
      d[25] = f(st, 1, 1);
      d[26] = f(st, 2, 1);
      if (!n.optBoolean("visible", true)) d[27] = 0;
      int idx = nodes.size();
      nodes.add(d);
      nodeIndex.put(n.optString("id", "n" + i), idx);
      addAnimations(n, idx, anim);

      String label = n.optString("label", "");
      if (kind.equals("quad") && !label.isEmpty() && !n.has("texture")) {
        // A quad with a label and no texture IS a text panel: it shows the label and faces the viewer.
        d[18] = texIndex(texKeys, "label:" + label);
        d[19] = ((int) d[19]) | 1;
        d[4] = Math.min(d[4], 0.35f);
        d[6] = d[7] = d[8] = d[9] = 1;
      } else if (!label.isEmpty()) {
        float[] l = newNode(2);
        l[1] = 0;
        l[2] = d[4] * 1.35f + 0.18f;
        l[4] = 0.14f;
        l[6] = l[7] = l[8] = l[9] = 1;
        l[11] = idx;
        l[18] = texIndex(texKeys, "label:" + label);
        l[19] = 1 | 8;  // billboard + auto-label (shown only for the highlighted / looked-at node)
        nodes.add(l);
      }
    }
    for (int index = 0; index < arr.length(); index++) {
      JSONObject node = arr.getJSONObject(index);
      Integer nativeIndex = nodeIndex.get(node.optString("id", "n" + index));
      if (nativeIndex != null) VisualEffects.pack(node, nodes.get(nativeIndex), nodeIndex);
    }
    VisualEffects.enforceBudgets(nodes);
    fogData = VisualEffects.fog(sc);
    if (extra != null) nodes.addAll(extra);
    animData = new float[anim.size()];
    for (int i = 0; i < animData.length; i++) animData[i] = anim.get(i);
    pushScene(nodes, texKeys);
  }

  private static int propId(String p) {
    switch (p) {
      case "scale": return 1;
      case "rotation": return 2;
      case "opacity": return 3;
      default: return 0;  // position
    }
  }

  private static float[] vecOf(Object o, float def) {
    float[] v = {def, def, def};
    if (o instanceof JSONArray) {
      JSONArray a = (JSONArray) o;
      for (int i = 0; i < 3; i++) v[i] = (float) a.optDouble(i, def);
    } else if (o instanceof Number) {
      v[0] = v[1] = v[2] = ((Number) o).floatValue();
    }
    return v;
  }

  /** Declarative, subject-independent motion: `animate` (looping property tracks) and `path` (polyline). */
  private void addAnimations(JSONObject n, int idx, List<Float> out) {
    JSONArray tracks = n.optJSONArray("animate");
    for (int i = 0; tracks != null && i < tracks.length(); i++) {
      JSONObject t = tracks.optJSONObject(i);
      if (t == null) continue;
      String prop = t.optString("prop", "position");
      float neutral = prop.equals("scale") || prop.equals("opacity") ? 1f : 0f;
      float[] from = vecOf(t.opt("from"), neutral), to = vecOf(t.opt("to"), neutral);
      String loop = t.optString("loop", "pingpong");
      out.add(0f); out.add((float) idx); out.add((float) propId(prop));
      out.add(loop.equals("once") ? 0f : loop.equals("loop") ? 1f : 2f);
      out.add(t.optString("ease", "smooth").equals("linear") ? 0f : 1f);
      out.add((float) t.optDouble("seconds", 2)); out.add((float) t.optDouble("delay", 0));
      for (float f : from) out.add(f);
      for (float f : to) out.add(f);
    }
    JSONObject path = n.optJSONObject("path");
    JSONArray pts = path == null ? null : path.optJSONArray("points");
    if (pts != null && pts.length() >= 2) {
      out.add(1f); out.add((float) idx);
      String loop = path.optString("loop", "loop");
      out.add(loop.equals("once") ? 0f : loop.equals("loop") ? 1f : 2f);
      out.add((float) path.optDouble("seconds", 6));
      out.add((float) path.optDouble("phase", 0));
      out.add((float) pts.length());
      for (int i = 0; i < pts.length(); i++) {
        JSONArray p = pts.optJSONArray(i);
        out.add(f(p, 0, 0)); out.add(f(p, 1, 0)); out.add(f(p, 2, 0));
      }
    }
  }

  /** Step actions: `set` tweens a node's position/scale/opacity; `show`/`hide` are opacity shorthands. */
  private void applyActions(JSONObject step, boolean remember, float secondsOverride) {
    JSONArray bursts = step.optJSONArray("burst");
    for (int index = 0; bursts != null && index < bursts.length(); index++) {
      Integer node = nodeIndex.get(bursts.optString(index));
      if (node != null) r.burst(node);
    }
    JSONArray sets = step.optJSONArray("set");
    for (int i = 0; sets != null && i < sets.length(); i++) applySet(sets.optJSONObject(i), remember, secondsOverride);
    for (String key : new String[] {"show", "hide"}) {
      JSONArray ids = step.optJSONArray(key);
      for (int i = 0; ids != null && i < ids.length(); i++) {
        JSONObject s = new JSONObject();
        try {
          s.put("node", ids.optString(i)).put("opacity", key.equals("show") ? 1 : 0).put("seconds", 0.8);
        } catch (JSONException ignored) {
        }
        applySet(s, remember, secondsOverride);
      }
    }
  }

  private void applySet(JSONObject s, boolean remember, float secondsOverride) {
    if (s == null) return;
    Integer idx = nodeIndex.get(s.optString("node", ""));
    if (idx == null) return;
    float sec = secondsOverride >= 0 ? secondsOverride : (float) s.optDouble("seconds", 1.0);
    if (s.has("position")) {
      float[] v = vecOf(s.opt("position"), 0f);
      r.tweenNode(idx, 0, v[0], v[1], v[2], sec);
    }
    if (s.has("scale")) r.tweenNode(idx, 1, (float) s.optDouble("scale", 1), 0, 0, sec);
    if (s.has("opacity")) r.tweenNode(idx, 2, (float) s.optDouble("opacity", 1), 0, 0, sec);
    if (remember) appliedSets.add(s);
  }

  private void pushScene(List<float[]> nodes, List<String> texKeys) {
    float[] flat = new float[nodes.size() * STRIDE];
    for (int i = 0; i < nodes.size(); i++) System.arraycopy(nodes.get(i), 0, flat, i * STRIDE, STRIDE);
    r.setScene(flat, texKeys.toArray(new String[0]));
    r.setFog(fogData);
    r.setAnimations(animData);
  }

  private static float[] newNode(int kind) {
    float[] d = new float[STRIDE];
    d[0] = kind;
    d[4] = 1;
    d[6] = d[7] = d[8] = d[9] = 1;
    d[11] = -1;
    d[17] = -1;
    d[18] = -1;
    d[24] = d[25] = d[26] = 1;
    d[27] = 1;  // initial opacity (0 = hidden until a step shows it)
    return d;
  }

  private static int texIndex(List<String> keys, String key) {
    int i = keys.indexOf(key);
    if (i >= 0) return i;
    keys.add(key);
    return keys.size() - 1;
  }

  // ---- guided steps -----------------------------------------------------
  private void runStep() {
    try {
      JSONArray steps = scene.optJSONArray("steps");
      if (steps == null || stepIndex >= steps.length()) {
        if (scene.optJSONObject("quiz") != null) runQuiz();
        else startScene(sceneIndex + 1);
        return;
      }
      JSONObject s = steps.getJSONObject(stepIndex++);
      Log.i(TAG, "step " + (stepIndex - 1) + " of scene " + scene.optString("id") + " starts");
      applyActions(s, true, -1f);
      JSONObject cam = s.optJSONObject("camera");
      if (cam != null) {
        JSONArray p = cam.optJSONArray("pos");
        Integer focus = nodeIndex.get(cam.optString("focus", ""));
        Integer follow = nodeIndex.get(cam.optString("follow", ""));
        // With "follow", pos is an offset from that node; otherwise an absolute position.
        // The first viewpoint of a scene is placed under a fade-in and aligns the world once (seconds < 0);
        // later changes are a short blink; the player never slides the camera.
        r.moveCamera(f(p, 0, 0), f(p, 1, follow == null ? 1.6f : 0f), f(p, 2, 0),
            focus == null ? -1 : focus, stepIndex == 1 ? -1f : 1f,
            follow == null ? -1 : follow);
      }
      Integer hl = nodeIndex.get(s.optString("highlight", ""));
      r.setHighlight(hl == null ? -1 : hl);
      speak(s.optString("text", ""), s.optString("audio", ""), s.optDouble("duration", 0),
          this::runStep);
      // After speak(): speak() clears pending callbacks, which would cancel the effect and the cues.
      playSfx(s.optJSONObject("sfx"));
      scheduleCues(s);
    } catch (JSONException e) {
      Log.e(TAG, "bad step", e);
    }
  }

  private void speak(String text, String audio, double duration, Runnable then) {
    r.setSubtitle(text);
    handler.removeCallbacksAndMessages(null);
    File f = audio.isEmpty() ? null : new File(dir, audio);
    if (f != null && f.exists()) {
      try {
        if (media != null) media.release();
        media = new MediaPlayer();
        media.setDataSource(f.getAbsolutePath());
        media.setOnCompletionListener(
            m -> handler.postDelayed(then, 600));
        media.prepare();
        media.start();
        return;
      } catch (Exception e) {
        Log.e(TAG, "audio failed: " + f, e);
      }
    }
    long ms = duration > 0 ? (long) (duration * 1000) : Math.max(2500, text.length() * 60L);
    handler.postDelayed(then, ms);
  }

  // ---- quiz (gaze selection) --------------------------------------------
  private void runQuiz() throws JSONException {
    inQuiz = true;
    quizAcceptingAnswers = false;
    r.setGazeEnabled(false);
    quiz = scene.getJSONObject("quiz");
    JSONArray opts = quiz.getJSONArray("options");
    optionCorrect = new int[opts.length()];
    // Rebuild scene with option panels placed around the first stop's heading.
    buildSceneWithOptions(opts);
    Log.i(TAG, "quiz question of scene " + scene.optString("id") + " starts");
    speak(quiz.optString("question"), quiz.optString("audio", ""), 0,
        () -> {
          quizAcceptingAnswers = true;
          Log.i(TAG, "quiz ready for answers");
          r.setGazeEnabled(true);
        });
  }

  private void buildSceneWithOptions(JSONArray opts) throws JSONException {
    // Keep current nodes: rebuild and append option panels at fixed spots in front of the rig's
    // last target (rig position is the camera pos of the last step; panels are placed on a ring
    // around it and the camera yaw is aligned to the middle panel).
    List<float[]> extra = new ArrayList<>();
    List<String> texKeys = new ArrayList<>();
    JSONArray steps = scene.optJSONArray("steps");
    float cx = 0, cy = 1.6f, cz = 0;
    if (steps != null && steps.length() > 0) {
      JSONObject cam = steps.getJSONObject(steps.length() - 1).optJSONObject("camera");
      if (cam != null) {
        JSONArray p = cam.optJSONArray("pos");
        cx = f(p, 0, 0);
        cy = f(p, 1, 1.6f);
        cz = f(p, 2, 0);
      }
    }
    // Panels sit at eye height above the head-locked caption, on an arc 2.2 m from the camera, centred on the
    // direction of the scene's first node.
    float ox = 0, oz = -4;
    JSONArray first = scene.optJSONArray("nodes");
    if (first != null && first.length() > 0) {
      JSONArray fp = first.getJSONObject(0).optJSONArray("pos");
      ox = f(fp, 0, 0);
      oz = f(fp, 2, -4);
    }
    double heading = Math.atan2(ox - cx, -(oz - cz));
    int n = opts.length();
    for (int i = 0; i < n; i++) {
      JSONObject o = opts.getJSONObject(i);
      double a = heading + (i - (n - 1) / 2.0) * 0.46;
      float[] d = newNode(2);
      d[1] = cx + (float) (Math.sin(a) * 2.2);
      d[2] = cy;
      d[3] = cz - (float) (Math.cos(a) * 2.2);
      d[4] = 0.16f;
      d[17] = 1000 + i;
      d[18] = texIndex(texKeys, "option:" + o.getString("label"));
      d[19] = 1;
      extra.add(d);
      optionCorrect[i] = o.optBoolean("correct", false) ? 1 : 0;
    }
    // Rebuild base scene, then append panels (texKeys merge handled by native index offset).
    List<float[]> nodes = new ArrayList<>();
    List<String> keys = new ArrayList<>();
    buildSceneInto(scene, nodes, keys);
    for (float[] d : extra) {
      d[18] = texIndex(keys, texKeys.get((int) d[18]));
      nodes.add(d);
    }
    int panelCenter = nodes.size();
    float[] headingAnchor = newNode(0);
    headingAnchor[1] = cx + (float) (Math.sin(heading) * 2.2);
    headingAnchor[2] = cy;
    headingAnchor[3] = cz - (float) (Math.cos(heading) * 2.2);
    headingAnchor[4] = 0;
    nodes.add(headingAnchor);
    pushScene(nodes, keys);
    for (JSONObject action : new ArrayList<>(appliedSets)) applySet(action, false, 0f);
    r.moveCamera(cx, cy, cz, panelCenter, -1f, -1);
    r.setHighlight(-1);
  }

  private void buildSceneInto(JSONObject sc, List<float[]> outNodes, List<String> outKeys)
      throws JSONException {
    // Reuse buildScene, then read back what was pushed.
    final float[][] captured = new float[1][];
    final String[][] capturedKeys = new String[1][];
    Renderer saved = new Renderer() {
      public void setScene(float[] nodes, String[] k) { captured[0] = nodes; capturedKeys[0] = k; }
      public void moveCamera(float x, float y, float z, int fo, float s, int fl) {}
      public void setHighlight(int n) {}
      public void setSubtitle(String t) {}
      public void setGazeEnabled(boolean e) {}
      public void setAnimations(float[] d) {}
      public void setFog(float[] data) {}
      public void burst(int node) {}
      public void tweenNode(int n, int p, float x, float y, float z, float s) {}
    };
    ExperiencePlayer tmp = new ExperiencePlayer(dir, saved);
    tmp.manifest = manifest;
    tmp.buildScene(sc, null);
    animData = tmp.animData;
    fogData = tmp.fogData;
    for (int i = 0; i < captured[0].length / STRIDE; i++) {
      float[] d = new float[STRIDE];
      System.arraycopy(captured[0], i * STRIDE, d, 0, STRIDE);
      outNodes.add(d);
    }
    for (String k : capturedKeys[0]) outKeys.add(k);
    nodeIndex.clear();
    nodeIndex.putAll(tmp.nodeIndex);
  }

  /** Opens the in-VR menu in front of the viewer (called when they look straight down). */
  void openMenu(float rx, float ry, float rz, float hx, float hz) {
    handler.post(
        () -> {
          if (menuOpen || finished || scene == null) return;
          menuOpen = true;
          handler.removeCallbacksAndMessages(null);
          if (media != null && media.isPlaying()) media.pause();
          try {
            List<float[]> nodes = new ArrayList<>();
            List<String> keys = new ArrayList<>();
            buildSceneInto(scene, nodes, keys);
            String[] labels = {"Resume", "All experiences"};
            for (int i = 0; i < 2; i++) {
              float side = i == 0 ? -0.62f : 0.62f;
              float[] d = newNode(2);
              d[1] = rx + hx * 2.2f - hz * side;
              d[2] = ry - 0.1f;
              d[3] = rz + hz * 2.2f + hx * side;
              d[4] = 0.14f;
              d[17] = 2000 + i;
              d[18] = texIndex(keys, "option:" + labels[i]);
              d[19] = 1;
              nodes.add(d);
            }
            pushScene(nodes, keys);
            r.moveCamera(rx, ry, rz, nodes.size() - 2, 0.3f, -1);  // face the menu
            r.setHighlight(-1);
            r.setSubtitle("Look at a choice");
            r.setGazeEnabled(true);
          } catch (JSONException e) {
            Log.e(TAG, "menu failed", e);
            menuOpen = false;
          }
        });
  }

  private void closeMenu(boolean resume) {
    menuOpen = false;
    if (!resume) {
      if (onExit != null) onExit.run();
      return;
    }
    try {
      buildScene(scene, null);
      for (JSONObject s : new ArrayList<>(appliedSets)) applySet(s, false, 0f);  // restore the scene state
    } catch (JSONException e) {
      Log.e(TAG, "rebuild failed", e);
    }
    r.setGazeEnabled(false);
    if (inQuiz) {
      try {
        runQuiz();
      } catch (JSONException e) {
        Log.e(TAG, "quiz resume failed", e);
      }
    } else {
      stepIndex = Math.max(0, stepIndex - 1);  // replay the step that was interrupted
      runStep();
    }
  }

  void onGaze(int pickId) {
    handler.post(
        () -> {
          if (pickId == 2000 || pickId == 2001) {
            if (menuOpen) closeMenu(pickId == 2000);
            return;
          }
          if (!inQuiz || !quizAcceptingAnswers || menuOpen || quiz == null || pickId < 1000) return;
          int i = pickId - 1000;
          if (optionCorrect == null || i >= optionCorrect.length) return;
          quizAcceptingAnswers = false;
          r.setGazeEnabled(false);
          boolean ok = optionCorrect[i] == 1;
          Log.i(TAG, "quiz answer " + i + " is " + (ok ? "correct" : "wrong"));
          JSONObject fb = quiz.optJSONObject(ok ? "correct" : "wrong");
          String text = fb != null ? fb.optString("text", ok ? "Correct!" : "Try again.")
                                   : (ok ? "Correct!" : "Try again.");
          String audio = fb != null ? fb.optString("audio", "") : "";
          speak(text, audio, 0, () -> {
            if (ok) {
              quiz = null;
              inQuiz = false;
              startScene(sceneIndex + 1);
            } else {
              quizAcceptingAnswers = true;
              Log.i(TAG, "quiz retry ready");
              r.setGazeEnabled(true);
            }
          });
        });
  }

  boolean isFinished() {
    return finished;
  }
}
