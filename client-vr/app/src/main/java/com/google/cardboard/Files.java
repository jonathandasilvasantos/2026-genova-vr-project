package com.google.cardboard;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;

final class Files {
  static String read(File f) throws IOException {
    try (InputStream in = new FileInputStream(f)) {
      ByteArrayOutputStream out = new ByteArrayOutputStream();
      byte[] buf = new byte[8192];
      int n;
      while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
      return out.toString("UTF-8");
    }
  }

  static void write(File f, String text) throws IOException {
    try (java.io.FileOutputStream out = new java.io.FileOutputStream(f)) {
      out.write(text.getBytes("UTF-8"));
    }
  }

  private Files() {}
}
