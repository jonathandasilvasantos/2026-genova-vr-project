package com.google.cardboard;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.json.JSONArray;
import org.json.JSONObject;

public final class VisualEffectsTest {
  private static void require(boolean condition, String message) {
    if (!condition) throw new AssertionError(message);
  }

  public static void main(String[] arguments) throws Exception {
    Map<String, Integer> indices = new HashMap<>();
    indices.put("target", 12);
    JSONObject catalog = new JSONObject(EffectsCatalog.DATA);
    for (String preset : new String[] {"sparks", "fire", "smoke", "steam", "dust", "explosion",
        "rain", "snow", "bubbles", "magic", "mist"}) {
      JSONObject node = new JSONObject().put("emitter", new JSONObject().put("preset", preset));
      float[] packed = new float[VisualEffects.STRIDE];
      packed[0] = VisualEffects.EMITTER;
      VisualEffects.pack(node, packed, indices);
      JSONObject expected = catalog.getJSONObject("emitters").getJSONObject(preset);
      require(packed[32] == expected.getInt("count"), "preset particle count " + preset);
      require(Math.abs(packed[33] - expected.getDouble("life")) < 0.00001, "preset particle lifetime " + preset);
      require(packed[47] >= 0 && packed[47] <= 1, "start color " + preset);
    }
    for (String preset : new String[] {"candle", "torch", "lamp", "spotlight", "moonlight", "alarm"}) {
      JSONObject node = new JSONObject().put("light", new JSONObject().put("preset", preset).put("aim", "target"));
      float[] packed = new float[VisualEffects.STRIDE];
      packed[0] = VisualEffects.LIGHT;
      VisualEffects.pack(node, packed, indices);
      require(packed[45] == 12, "forward aim reference " + preset);
      require(packed[37] > 0, "light range " + preset);
    }
    JSONObject macro = new JSONObject("{\"id\":\"event\",\"kind\":\"effect\",\"effect\":\"explosion\",\"pos\":[1,2,3]}");
    JSONArray expanded = VisualEffects.expand(new JSONArray().put(macro));
    require(expanded.length() == 5, "group + four explosion children");
    require(expanded.getJSONObject(1).getString("parent").equals("event"), "effect hierarchy");
    require(macro.getString("kind").equals("effect"), "input is not mutated");
    List<float[]> nodes = new ArrayList<>();
    for (int index = 0; index < 10; index++) {
      float[] packed = new float[VisualEffects.STRIDE];
      packed[0] = VisualEffects.EMITTER;
      VisualEffects.pack(new JSONObject("{\"emitter\":{\"count\":9999,\"loop\":false}}"), packed, indices);
      require(packed[32] == 400 && packed[56] == 0, "per-emitter cap and one-shot flag");
      nodes.add(packed);
      float[] light = new float[VisualEffects.STRIDE];
      light[0] = VisualEffects.LIGHT;
      nodes.add(light);
    }
    VisualEffects.enforceBudgets(nodes);
    int emitters = 0, lights = 0, total = 0;
    for (float[] node : nodes) {
      if (node[0] == VisualEffects.EMITTER) { emitters++; total += (int) node[32]; }
      if (node[0] == VisualEffects.LIGHT) lights++;
    }
    require(emitters == 6 && lights == 4 && total == 1500, "scene caps");
    require(VisualEffects.fog(new JSONObject())[3] == 0, "fog defaults off");
    float[] fog = VisualEffects.fog(new JSONObject("{\"fog\":{\"density\":4,\"falloff\":9,\"start\":200}}"));
    require(fog[3] == 0.5f && fog[5] == 5 && fog[6] == 100, "fog caps");
    System.out.println("PASS: all presets, macro expansion, forward light targets, packing, particle/light budgets, fog");
  }
}
