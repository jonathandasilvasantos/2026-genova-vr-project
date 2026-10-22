package com.google.cardboard;

import org.json.JSONException;
import org.json.JSONObject;

/** One published experience as listed by the server. */
final class Remote {
  final String id, title, summary, language, url, sha256;
  final int version, scenes, durationS;
  final long size;

  Remote(JSONObject o) throws JSONException {
    id = o.getString("id");
    title = o.getString("title");
    summary = o.optString("summary", "");
    language = o.optString("language", "");
    url = o.getString("url");
    sha256 = o.optString("sha256", "");
    version = o.getInt("version");
    scenes = o.optInt("scenes");
    durationS = o.optInt("duration_s");
    size = o.optLong("size");
  }
}
