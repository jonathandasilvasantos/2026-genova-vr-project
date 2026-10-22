package com.google.cardboard;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;
import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.json.JSONArray;
import org.json.JSONObject;

/** Server access and the on-device copy of downloaded experiences (app-private storage). */
final class Store {
  private static final String TAG = "GenovaStore";
  interface Progress {
    void onProgress(long done, long total);
  }

  private final Context ctx;
  private final SharedPreferences prefs;

  Store(Context ctx) {
    this.ctx = ctx;
    this.prefs = ctx.getSharedPreferences("genova", Context.MODE_PRIVATE);
  }

  String serverUrl() {
    return prefs.getString("server", BuildConfig.SERVER_URL);
  }

  void setServerUrl(String url) {
    prefs.edit().putString("server", url.trim().replaceAll("/+$", "")).apply();
  }

  File dir(String id) {
    return new File(ctx.getFilesDir(), "experiences/" + id);
  }

  /** Installed version of an experience, or 0. */
  int installedVersion(String id) {
    try {
      return new JSONObject(Files.read(new File(dir(id), "installed.json"))).getInt("version");
    } catch (Exception e) {
      return 0;
    }
  }

  /** Metadata saved at install time, so downloaded experiences are listed even when offline. */
  Remote installedMeta(String id) {
    try {
      return new Remote(new JSONObject(Files.read(new File(dir(id), "installed.json"))).getJSONObject("meta"));
    } catch (Exception e) {
      return null;
    }
  }

  List<String> installedIds() {
    List<String> ids = new ArrayList<>();
    File[] fs = new File(ctx.getFilesDir(), "experiences").listFiles();
    if (fs != null) for (File f : fs) if (f.isDirectory() && !f.getName().endsWith(".tmp")) ids.add(f.getName());
    return ids;
  }

  void delete(String id) {
    deleteRecursive(dir(id));
  }

  /** Fetches the published list; caches it for offline use. */
  List<Remote> fetchList() throws Exception {
    HttpURLConnection c = (HttpURLConnection) new URL(serverUrl() + "/api/v1/experiences").openConnection();
    c.setConnectTimeout(8000);
    c.setReadTimeout(15000);
    try (InputStream in = c.getInputStream()) {
      String body = new String(readAll(in), "UTF-8");
      JSONArray arr = new JSONObject(body).getJSONArray("experiences");
      prefs.edit().putString("list_cache", body).apply();
      return parse(arr);
    } finally {
      c.disconnect();
    }
  }

  List<Remote> cachedList() {
    try {
      return parse(new JSONObject(prefs.getString("list_cache", "{\"experiences\":[]}")).getJSONArray("experiences"));
    } catch (Exception e) {
      return new ArrayList<>();
    }
  }

  private static List<Remote> parse(JSONArray arr) throws Exception {
    List<Remote> out = new ArrayList<>();
    for (int i = 0; i < arr.length(); i++) out.add(new Remote(arr.getJSONObject(i)));
    return out;
  }

  /** Downloads, verifies and installs a bundle; the previous version stays until the new one is complete. */
  void install(Remote r, Progress progress) throws Exception {
    File tmpZip = new File(ctx.getCacheDir(), r.id + ".zip");
    HttpURLConnection c = (HttpURLConnection) new URL(r.url).openConnection();
    c.setConnectTimeout(10000);
    c.setReadTimeout(30000);
    MessageDigest sha = MessageDigest.getInstance("SHA-256");
    long total = c.getContentLengthLong() > 0 ? c.getContentLengthLong() : r.size;
    try (InputStream in = new BufferedInputStream(c.getInputStream()); FileOutputStream out = new FileOutputStream(tmpZip)) {
      byte[] buf = new byte[64 * 1024];
      long done = 0;
      int n;
      while ((n = in.read(buf)) > 0) {
        out.write(buf, 0, n);
        sha.update(buf, 0, n);
        done += n;
        progress.onProgress(done, total);
      }
    } finally {
      c.disconnect();
    }
    if (!r.sha256.isEmpty()) {
      StringBuilder hex = new StringBuilder();
      for (byte b : sha.digest()) hex.append(String.format("%02x", b));
      if (!hex.toString().equalsIgnoreCase(r.sha256)) {
        tmpZip.delete();
        throw new IOException("download corrupted (checksum mismatch)");
      }
    }
    File tmp = new File(dir(r.id).getParentFile(), r.id + ".tmp");
    deleteRecursive(tmp);
    unzip(tmpZip, tmp);
    tmpZip.delete();
    JSONObject meta = new JSONObject();
    meta.put("id", r.id).put("title", r.title).put("summary", r.summary).put("language", r.language)
        .put("url", r.url).put("sha256", r.sha256).put("version", r.version).put("scenes", r.scenes)
        .put("duration_s", r.durationS).put("size", r.size);
    Files.write(new File(tmp, "installed.json"), new JSONObject().put("version", r.version).put("meta", meta).toString());
    deleteRecursive(dir(r.id));
    if (!tmp.renameTo(dir(r.id))) throw new IOException("could not install " + r.id);
    Log.i(TAG, "installed " + r.id + " v" + r.version);
  }

  private static void unzip(File zip, File dest) throws IOException {
    dest.mkdirs();
    String root = dest.getCanonicalPath() + File.separator;
    try (ZipInputStream z = new ZipInputStream(new BufferedInputStream(new java.io.FileInputStream(zip)))) {
      ZipEntry e;
      byte[] buf = new byte[64 * 1024];
      while ((e = z.getNextEntry()) != null) {
        File f = new File(dest, e.getName());
        if (!f.getCanonicalPath().startsWith(root)) throw new IOException("bad zip entry " + e.getName());
        if (e.isDirectory()) {
          f.mkdirs();
          continue;
        }
        f.getParentFile().mkdirs();
        try (FileOutputStream out = new FileOutputStream(f)) {
          int n;
          while ((n = z.read(buf)) > 0) out.write(buf, 0, n);
        }
      }
    }
  }

  private static byte[] readAll(InputStream in) throws IOException {
    java.io.ByteArrayOutputStream o = new java.io.ByteArrayOutputStream();
    byte[] b = new byte[8192];
    int n;
    while ((n = in.read(b)) > 0) o.write(b, 0, n);
    return o.toByteArray();
  }

  static void deleteRecursive(File f) {
    if (f == null || !f.exists()) return;
    File[] kids = f.listFiles();
    if (kids != null) for (File k : kids) deleteRecursive(k);
    f.delete();
  }
}
