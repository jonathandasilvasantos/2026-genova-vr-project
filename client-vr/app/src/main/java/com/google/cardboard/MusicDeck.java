package com.google.cardboard;

import android.media.MediaPlayer;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import java.io.File;
import java.util.ArrayList;
import java.util.List;

/**
 * Background music with crossfades. Changing track fades the old one out while the new one fades
 * in; a track shorter than the experience loops by crossfading into itself, so there is no click.
 * Playing the file that is already playing only adjusts the volume (no restart).
 */
final class MusicDeck {
  private static final String TAG = "MusicDeck";
  private static final int STEP_MS = 60;

  private final Handler handler = new Handler(Looper.getMainLooper());
  private final int fadeMs;
  private final List<Voice> voices = new ArrayList<>();
  private String currentPath;
  private float currentVolume;
  private int generation;

  private final class Voice {
    final MediaPlayer player;
    float level;      // 0..1 fade envelope
    float target;     // envelope target
    boolean dead;

    Voice(MediaPlayer p) {
      player = p;
    }

    /** Equal-power curve: a fade-out and a fade-in that sum to constant loudness. */
    float gain() {
      return (float) Math.sin(level * Math.PI / 2) * currentVolume;
    }
  }

  MusicDeck(int fadeMs) {
    this.fadeMs = fadeMs;
  }

  /** Start (or keep) the track at `file` at the given master volume (0..1). */
  void play(File file, float volume) {
    if (file == null || !file.exists()) return;
    String path = file.getAbsolutePath();
    currentVolume = volume;
    if (path.equals(currentPath) && !voices.isEmpty()) {
      applyGains();
      return;
    }
    currentPath = path;
    generation++;
    fadeOutAll();
    startVoice(path, generation);
  }

  /** Fade everything out. */
  void stop() {
    currentPath = null;
    generation++;
    fadeOutAll();
  }

  void release() {
    handler.removeCallbacksAndMessages(null);
    for (Voice v : voices) v.player.release();
    voices.clear();
    currentPath = null;
  }

  private void fadeOutAll() {
    for (Voice v : voices) v.target = 0f;
    tick();
  }

  private void startVoice(final String path, final int gen) {
    try {
      MediaPlayer p = new MediaPlayer();
      p.setDataSource(path);
      p.prepare();
      final Voice v = new Voice(p);
      v.level = 0f;
      v.target = 1f;
      p.setVolume(0f, 0f);
      p.start();
      voices.add(v);
      Log.i(TAG, "start voice " + new File(path).getName() + " dur=" + p.getDuration() + "ms voices=" + voices.size());
      tick();
      // Loop by crossfading into a fresh voice shortly before the end.
      int dur = p.getDuration();
      long wait = Math.max(1000, dur - fadeMs - 200);
      handler.postDelayed(
          () -> {
            Log.i(TAG, "loop check dead=" + v.dead + " gen=" + gen + "/" + generation + " same=" + path.equals(currentPath));
            if (!v.dead && gen == generation && path.equals(currentPath)) {
              v.target = 0f;
              startVoice(path, gen);
            }
          },
          wait);
    } catch (Exception e) {
      Log.e(TAG, "music failed: " + path, e);
    }
  }

  private void applyGains() {
    for (Voice v : voices) v.player.setVolume(v.gain(), v.gain());
  }

  private final Runnable ticker = this::tick;

  private void tick() {
    handler.removeCallbacks(ticker);
    final float delta = STEP_MS / (float) fadeMs;
    boolean busy = false;
    for (int i = voices.size() - 1; i >= 0; i--) {
      Voice v = voices.get(i);
      if (v.level < v.target) v.level = Math.min(v.target, v.level + delta);
      else if (v.level > v.target) v.level = Math.max(v.target, v.level - delta);
      if (v.level != v.target) busy = true;
      if (v.target == 0f && v.level == 0f) {
        v.dead = true;
        v.player.release();
        voices.remove(i);
      } else {
        v.player.setVolume(v.gain(), v.gain());
      }
    }
    if (busy) handler.postDelayed(ticker, STEP_MS);
  }
}
