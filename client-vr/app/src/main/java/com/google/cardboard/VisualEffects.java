package com.google.cardboard;

import java.util.Iterator;
import java.util.List;
import java.util.Map;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

final class VisualEffects {
  static final int STRIDE = 64;
  static final int EMITTER = 9;
  static final int LIGHT = 10;
  static final int GROUP = 11;
  static final int MAX_EMITTERS = 6;
  static final int MAX_LIGHTS = 4;
  static final int MAX_PARTICLES = 1500;
  private static final JSONObject CATALOG;

  static {
    try {
      CATALOG = new JSONObject(EffectsCatalog.DATA);
    } catch (JSONException exception) {
      throw new IllegalStateException(exception);
    }
  }

  private VisualEffects() {}

  static JSONArray expand(JSONArray source) throws JSONException {
    JSONArray expanded = new JSONArray();
    for (int index = 0; source != null && index < source.length(); index++) {
      JSONObject node = source.getJSONObject(index);
      if (!node.optString("kind").equals("effect")) {
        expanded.put(node);
        continue;
      }
      JSONObject group = new JSONObject(node.toString());
      group.put("kind", "effect");
      expanded.put(group);
      JSONArray parts = CATALOG.getJSONObject("macros").optJSONArray(node.optString("effect", "explosion"));
      for (int part = 0; parts != null && part < parts.length(); part++) {
        JSONArray definition = parts.getJSONArray(part);
        String kind = definition.getString(1);
        JSONObject child = new JSONObject();
        String identifier = node.getString("id") + "_" + definition.getString(0);
        child.put("id", identifier.substring(0, Math.min(identifier.length(), 41)));
        child.put("parent", node.getString("id"));
        child.put("kind", kind);
        child.put("pos", new JSONArray("[0,0,0]"));
        child.put(kind, definition.getJSONObject(2));
        expanded.put(child);
      }
    }
    return expanded;
  }

  private static JSONObject parameters(JSONObject node, String kind) throws JSONException {
    JSONObject result = new JSONObject(CATALOG.getJSONObject(kind).toString());
    JSONObject overrides = node.optJSONObject(kind);
    JSONObject presets = CATALOG.getJSONObject(kind.equals("emitter") ? "emitters" : "lights");
    JSONObject preset = presets.optJSONObject(overrides == null ? "" : overrides.optString("preset"));
    merge(result, preset);
    merge(result, overrides);
    return result;
  }

  private static void merge(JSONObject target, JSONObject source) throws JSONException {
    if (source == null) return;
    Iterator<String> keys = source.keys();
    while (keys.hasNext()) {
      String key = keys.next();
      target.put(key, source.get(key));
    }
  }

  private static float number(JSONObject object, String key, float fallback, float minimum, float maximum) {
    double value = object.optDouble(key, fallback);
    return Double.isNaN(value) || Double.isInfinite(value) ? fallback
        : (float) Math.max(minimum, Math.min(maximum, value));
  }

  private static void vector(float[] packed, int offset, JSONArray values, float[] fallback) {
    for (int axis = 0; axis < fallback.length; axis++) {
      double value = values == null ? fallback[axis] : values.optDouble(axis, fallback[axis]);
      packed[offset + axis] = Double.isNaN(value) || Double.isInfinite(value) ? fallback[axis] : (float) value;
    }
  }

  private static int choice(String value, String... choices) {
    for (int index = 0; index < choices.length; index++) if (choices[index].equals(value)) return index;
    return 0;
  }

  static void pack(JSONObject node, float[] packed, Map<String, Integer> indices) throws JSONException {
    if (packed[0] == EMITTER) {
      JSONObject parameters = parameters(node, "emitter");
      packed[32] = number(parameters, "count", 60, 1, 400);
      packed[33] = number(parameters, "life", 2, 0.1f, 30);
      packed[34] = number(parameters, "speed", 1, 0, 30);
      packed[35] = number(parameters, "gravity", 0, -30, 30);
      packed[36] = number(parameters, "drag", 0, 0, 10);
      packed[37] = (float) Math.toRadians(number(parameters, "spread", 45, 0, 180));
      vector(packed, 38, parameters.optJSONArray("dir"), new float[] {0, 1, 0});
      packed[41] = choice(parameters.optString("shape"), "point", "sphere", "box", "disc", "line");
      vector(packed, 42, parameters.optJSONArray("extent"), new float[] {0, 0, 0});
      vector(packed, 45, parameters.optJSONArray("size"), new float[] {0.1f, 0.1f});
      vector(packed, 47, parameters.optJSONArray("color_start"), new float[] {1, 1, 1, 1});
      vector(packed, 51, parameters.optJSONArray("color_end"), new float[] {1, 1, 1, 0});
      packed[55] = parameters.optString("blend").equals("alpha") ? 0 : 1;
      packed[56] = parameters.optBoolean("loop", true) ? 1 : 0;
      packed[57] = choice(parameters.optString("texture"), "soft", "spark", "smoke", "ring", "star");
    } else if (packed[0] == LIGHT) {
      JSONObject parameters = parameters(node, "light");
      packed[32] = parameters.optString("type").equals("spot") ? 1 : 0;
      vector(packed, 33, parameters.optJSONArray("color"), new float[] {1, 1, 1});
      packed[36] = number(parameters, "intensity", 1, 0, 10);
      packed[37] = number(parameters, "range", 8, 0.5f, 60);
      packed[38] = (float) Math.toRadians(number(parameters, "angle", 35, 5, 80));
      packed[39] = number(parameters, "softness", 0.3f, 0, 1);
      vector(packed, 40, parameters.optJSONArray("dir"), new float[] {0, -1, 0});
      packed[43] = choice(parameters.optString("flicker"), "none", "candle", "pulse", "strobe", "flash");
      packed[44] = parameters.optBoolean("beam", false) ? 1 : 0;
      Integer aim = indices.get(parameters.optString("aim"));
      packed[45] = aim == null ? -1 : aim;
    }
  }

  static void enforceBudgets(List<float[]> nodes) {
    int emitters = 0;
    int lights = 0;
    int particles = 0;
    for (float[] node : nodes) {
      if (node[0] == EMITTER) {
        if (++emitters > MAX_EMITTERS) node[0] = GROUP;
        else particles += (int) node[32];
      } else if (node[0] == LIGHT && ++lights > MAX_LIGHTS) node[0] = GROUP;
    }
    if (particles > MAX_PARTICLES) {
      for (float[] node : nodes) if (node[0] == EMITTER)
        node[32] = Math.max(1, (int) (node[32] * MAX_PARTICLES / particles));
    }
  }

  static float[] fog(JSONObject scene) {
    float[] packed = {0.7f, 0.75f, 0.8f, 0, 0, 0, 0};
    JSONObject fog = scene.optJSONObject("fog");
    if (fog == null) return packed;
    vector(packed, 0, fog.optJSONArray("color"), new float[] {0.7f, 0.75f, 0.8f});
    packed[3] = number(fog, "density", 0, 0, 0.5f);
    packed[4] = number(fog, "base", 0, -10000, 10000);
    packed[5] = number(fog, "falloff", 0, 0, 5);
    packed[6] = number(fog, "start", 0, 0, 100);
    return packed;
  }
}
