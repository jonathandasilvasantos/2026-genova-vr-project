package com.google.cardboard;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import java.io.File;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Home screen: the list of experiences; download, update, play, delete. */
public class LauncherActivity extends Activity {
  private static final int BG = Color.parseColor("#0E1118"), CARD = Color.parseColor("#171C27"),
      LINE = Color.parseColor("#262D3D"), TEXT = Color.parseColor("#E8ECF4"), MUTE = Color.parseColor("#8D97AD"),
      ACCENT = Color.parseColor("#6EA8FF"), OK = Color.parseColor("#4CC38A"), WARN = Color.parseColor("#F2B24C");

  private Store store;
  private LinearLayout list;
  private TextView status;
  private final ExecutorService io = Executors.newFixedThreadPool(2);
  private final Set<String> downloading = new HashSet<>();
  private boolean offline;

  @Override
  protected void onCreate(Bundle b) {
    super.onCreate(b);
    store = new Store(this);
    getWindow().setStatusBarColor(BG);
    getWindow().setNavigationBarColor(BG);

    ScrollView scroll = new ScrollView(this);
    scroll.setBackgroundColor(BG);
    LinearLayout root = new LinearLayout(this);
    root.setOrientation(LinearLayout.VERTICAL);
    root.setPadding(dp(18), dp(28), dp(18), dp(40));
    scroll.addView(root);

    LinearLayout head = new LinearLayout(this);
    head.setGravity(Gravity.CENTER_VERTICAL);
    TextView title = text("Genova VR", 28, TEXT, true);
    title.setOnLongClickListener(v -> { askServer(); return true; });
    head.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
    Button refresh = button("Refresh", false);
    refresh.setOnClickListener(v -> refresh());
    head.addView(refresh);
    root.addView(head);
    status = text("", 13, MUTE, false);
    status.setPadding(0, dp(4), 0, dp(14));
    root.addView(status);
    list = new LinearLayout(this);
    list.setOrientation(LinearLayout.VERTICAL);
    root.addView(list);
    setContentView(scroll);
    // Dev shortcut: adb shell am start -n ai.genova.vr/com.google.cardboard.LauncherActivity --es play <id> [--ez debug_menu true]
    String path = getIntent().getStringExtra("bundle_path");
    if (path != null) {
      Intent i = new Intent(this, VrActivity.class);
      i.putExtra("bundle", path);
      i.putExtra("fixed_pose", getIntent().getBooleanExtra("fixed_pose", false));
      i.putExtra("fixed_pose_seconds", getIntent().getFloatExtra("fixed_pose_seconds", 90f));
      startActivity(i);
    }
    String play = getIntent().getStringExtra("play");
    if (play != null && store.installedVersion(play) > 0) {
      Intent i = new Intent(this, VrActivity.class);
      i.putExtra("bundle", store.dir(play).getAbsolutePath());
      i.putExtra("debug_menu", getIntent().getBooleanExtra("debug_menu", false));
      i.putExtra("scene", getIntent().getIntExtra("scene", 0));
      i.putExtra("fixed_pose", getIntent().getBooleanExtra("fixed_pose", false));
      startActivity(i);
    }
  }

  @Override
  protected void onResume() {
    super.onResume();
    refresh();
  }

  @Override
  protected void onDestroy() {
    super.onDestroy();
    io.shutdownNow();
  }

  private void refresh() {
    status.setText("Loading…");
    render(store.cachedList(), true);
    io.execute(() -> {
      List<Remote> remote = null;
      try {
        remote = store.fetchList();
        offline = false;
      } catch (Exception e) {
        offline = true;
      }
      final List<Remote> result = remote != null ? remote : store.cachedList();
      runOnUiThread(() -> render(result, false));
    });
  }

  private void render(List<Remote> remote, boolean loading) {
    list.removeAllViews();
    Set<String> shown = new HashSet<>();
    int n = 0;
    for (Remote r : remote) {
      shown.add(r.id);
      list.addView(card(r, store.installedVersion(r.id), true));
      n++;
    }
    // Downloaded experiences that are no longer on the server stay playable.
    for (String id : store.installedIds()) {
      if (shown.contains(id)) continue;
      Remote m = store.installedMeta(id);
      if (m != null) {
        list.addView(card(m, store.installedVersion(id), false));
        n++;
      }
    }
    if (!loading) {
      status.setText(offline ? "Offline — showing saved experiences" : n + " experience" + (n == 1 ? "" : "s") + " · " + store.serverUrl().replace("https://", ""));
    }
    if (n == 0 && !loading) {
      TextView t = text(offline ? "No connection and nothing downloaded yet." : "No experiences have been published yet.", 15, MUTE, false);
      t.setPadding(0, dp(20), 0, 0);
      list.addView(t);
    }
  }

  private View card(Remote r, int installed, boolean onServer) {
    LinearLayout c = new LinearLayout(this);
    c.setOrientation(LinearLayout.VERTICAL);
    c.setPadding(dp(16), dp(14), dp(16), dp(14));
    GradientDrawable bg = new GradientDrawable();
    bg.setColor(CARD);
    bg.setCornerRadius(dp(14));
    bg.setStroke(dp(1), LINE);
    c.setBackground(bg);
    LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
    lp.bottomMargin = dp(12);
    c.setLayoutParams(lp);

    c.addView(text(r.title, 18, TEXT, true));
    if (!r.summary.isEmpty()) {
      TextView s = text(r.summary, 14, MUTE, false);
      s.setMaxLines(3);
      s.setEllipsize(android.text.TextUtils.TruncateAt.END);
      s.setPadding(0, dp(4), 0, 0);
      c.addView(s);
    }
    String meta = r.scenes + " scenes · " + Math.max(1, Math.round(r.durationS / 60f)) + " min · " + (r.size / 1048576) + " MB";
    TextView m = text(meta, 12, MUTE, false);
    m.setPadding(0, dp(6), 0, dp(10));
    c.addView(m);

    LinearLayout row = new LinearLayout(this);
    row.setGravity(Gravity.CENTER_VERTICAL);
    boolean busy = downloading.contains(r.id);
    if (busy) {
      ProgressBar pb = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
      pb.setIndeterminate(false);
      pb.setMax(1000);
      pb.setTag(r.id);
      row.addView(pb, new LinearLayout.LayoutParams(0, dp(8), 1));
      progressBars.put(r.id, pb);
    } else if (installed > 0) {
      Button play = button("▶  Play", true);
      play.setOnClickListener(v -> play(r.id));
      row.addView(play);
      if (onServer && r.version > installed) {
        Button up = button("Update", false);
        up.setTextColor(WARN);
        up.setOnClickListener(v -> download(r));
        LinearLayout.LayoutParams ulp = new LinearLayout.LayoutParams(-2, -2);
        ulp.leftMargin = dp(8);
        row.addView(up, ulp);
      }
      Button del = button("Delete", false);
      del.setOnClickListener(v -> confirmDelete(r));
      LinearLayout.LayoutParams dlp = new LinearLayout.LayoutParams(-2, -2);
      dlp.leftMargin = dp(8);
      row.addView(del, dlp);
    } else {
      Button dl = button("Download", true);
      dl.setOnClickListener(v -> download(r));
      row.addView(dl);
    }
    c.addView(row);
    c.setOnLongClickListener(v -> { if (installed > 0) confirmDelete(r); return true; });
    return c;
  }

  private final java.util.Map<String, ProgressBar> progressBars = new java.util.HashMap<>();

  private void download(Remote r) {
    downloading.add(r.id);
    render(store.cachedList(), true);
    refreshCards();
    io.execute(() -> {
      try {
        store.install(r, (done, total) -> {
          final int p = total > 0 ? (int) (done * 1000 / total) : 0;
          runOnUiThread(() -> {
            ProgressBar pb = progressBars.get(r.id);
            if (pb != null) pb.setProgress(p);
          });
        });
        runOnUiThread(() -> Toast.makeText(this, r.title + " is ready", Toast.LENGTH_SHORT).show());
      } catch (Exception e) {
        runOnUiThread(() -> Toast.makeText(this, "Download failed: " + e.getMessage(), Toast.LENGTH_LONG).show());
      } finally {
        runOnUiThread(() -> {
          downloading.remove(r.id);
          refreshCards();
        });
      }
    });
  }

  private void refreshCards() {
    render(store.cachedList(), false);
  }

  private void play(String id) {
    File dir = store.dir(id);
    Intent i = new Intent(this, VrActivity.class);
    i.putExtra("bundle", dir.getAbsolutePath());
    startActivity(i);
  }

  private void confirmDelete(Remote r) {
    new AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
        .setTitle("Remove from this phone?")
        .setMessage(r.title + " will be deleted from the device. You can download it again later.")
        .setPositiveButton("Delete", (d, w) -> { store.delete(r.id); refreshCards(); })
        .setNegativeButton("Cancel", null)
        .show();
  }

  private void askServer() {
    final EditText in = new EditText(this);
    in.setText(store.serverUrl());
    in.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
    new AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
        .setTitle("Server address")
        .setView(in)
        .setPositiveButton("Save", (d, w) -> { store.setServerUrl(in.getText().toString()); refresh(); })
        .setNegativeButton("Cancel", null)
        .show();
  }

  // ---- small view helpers ---------------------------------------------------
  private TextView text(String s, float sp, int color, boolean bold) {
    TextView t = new TextView(this);
    t.setText(s);
    t.setTextSize(sp);
    t.setTextColor(color);
    if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
    return t;
  }

  private Button button(String label, boolean primary) {
    Button b = new Button(this);
    b.setText(label);
    b.setAllCaps(false);
    b.setTextSize(14);
    b.setTypeface(Typeface.DEFAULT_BOLD);
    GradientDrawable g = new GradientDrawable();
    g.setCornerRadius(dp(10));
    if (primary) {
      g.setColor(ACCENT);
      b.setTextColor(Color.parseColor("#06101F"));
    } else {
      g.setColor(Color.TRANSPARENT);
      g.setStroke(dp(1), LINE);
      b.setTextColor(TEXT);
    }
    b.setBackground(g);
    b.setPadding(dp(16), 0, dp(16), 0);
    b.setMinHeight(dp(40));
    b.setMinimumHeight(dp(40));
    return b;
  }

  private int dp(int v) {
    return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
  }
}
