package com.google.cardboard;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.opengl.GLES20;
import android.opengl.GLUtils;
import android.text.Layout;
import android.text.StaticLayout;
import android.text.TextPaint;
import java.io.File;

/** Creates GL textures on the render thread from keys: file:, label:, sub:, option:, builtin:. */
final class TextureFactory {
  private final File bundleDir;

  TextureFactory(File bundleDir) {
    this.bundleDir = bundleDir;
  }

  int[] create(String key) {
    Bitmap bmp = null;
    if (key.startsWith("file:")) {
      bmp = BitmapFactory.decodeFile(new File(bundleDir, key.substring(5)).getAbsolutePath());
    } else if (key.startsWith("label:")) {
      bmp = text(key.substring(6), 64, 0xAA000000, 0xFFFFFFFF, 640, false);
    } else if (key.startsWith("option:")) {
      bmp = text(key.substring(7), 72, 0xDD102040, 0xFFFFFFFF, 720, true);
    } else if (key.startsWith("sub:")) {
      bmp = text(key.substring(4), 44, 0xA0000000, 0xFFFFFFFF, 1100, false);
    } else if (key.equals("builtin:reticle")) {
      bmp = reticle();
    } else if (key.equals("builtin:arrow")) {
      bmp = arrow();
    }
    if (bmp == null) return null;
    int[] id = new int[1];
    GLES20.glGenTextures(1, id, 0);
    GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, id[0]);
    GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR);
    GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR);
    GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE);
    GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE);
    GLUtils.texImage2D(GLES20.GL_TEXTURE_2D, 0, bmp, 0);
    int w = bmp.getWidth(), h = bmp.getHeight();
    bmp.recycle();
    return new int[] {id[0], w, h};
  }

  private static Bitmap text(String s, float size, int bg, int fg, int width, boolean center) {
    TextPaint tp = new TextPaint(Paint.ANTI_ALIAS_FLAG);
    tp.setColor(fg);
    tp.setTextSize(size);
    tp.setTypeface(Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD));
    int pad = (int) (size * 0.5f);
    int inner = width - 2 * pad;
    StaticLayout layout =
        StaticLayout.Builder.obtain(s, 0, s.length(), tp, inner)
            .setAlignment(Layout.Alignment.ALIGN_CENTER)
            .build();
    int h = layout.getHeight() + 2 * pad;
    if (center) h = Math.max(h, (int) Math.ceil(width / 2.5f));
    Bitmap b = Bitmap.createBitmap(width, h, Bitmap.Config.ARGB_8888);
    Canvas c = new Canvas(b);
    Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
    p.setColor(bg);
    c.drawRoundRect(new RectF(0, 0, width, h), size * 0.6f, size * 0.6f, p);
    c.translate(pad, center ? (h - layout.getHeight()) / 2f : pad);
    layout.draw(c);
    return b;
  }

  /** Chevron pointing to the right (+x); the renderer rotates it toward the object. */
  private static Bitmap arrow() {
    int n = 128;
    Bitmap b = Bitmap.createBitmap(n, n, Bitmap.Config.ARGB_8888);
    Canvas c = new Canvas(b);
    Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
    p.setColor(Color.WHITE);
    android.graphics.Path path = new android.graphics.Path();
    path.moveTo(n * 0.15f, n * 0.15f);
    path.lineTo(n * 0.92f, n * 0.5f);
    path.lineTo(n * 0.15f, n * 0.85f);
    path.lineTo(n * 0.38f, n * 0.5f);
    path.close();
    c.drawPath(path, p);
    return b;
  }

  private static Bitmap reticle() {
    int n = 128;
    Bitmap b = Bitmap.createBitmap(n, n, Bitmap.Config.ARGB_8888);
    Canvas c = new Canvas(b);
    Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
    p.setStyle(Paint.Style.STROKE);
    p.setStrokeWidth(12);
    p.setColor(Color.WHITE);
    c.drawCircle(n / 2f, n / 2f, n / 2f - 10, p);
    p.setStyle(Paint.Style.FILL);
    c.drawCircle(n / 2f, n / 2f, 9, p);
    return b;
  }
}
