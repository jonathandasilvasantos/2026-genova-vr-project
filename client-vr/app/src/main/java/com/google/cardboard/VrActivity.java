/*
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.google.cardboard;

import android.Manifest;
import android.annotation.SuppressLint;
import android.content.Intent;
import android.content.pm.PackageManager;
import java.io.File;
import android.net.Uri;
import android.opengl.GLSurfaceView;
import android.os.Build.VERSION;
import android.os.Build.VERSION_CODES;
import android.os.Bundle;
import android.provider.Settings;
import androidx.appcompat.app.AppCompatActivity;
import android.util.Log;
import android.view.MenuInflater;
import android.view.MenuItem;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.PopupMenu;
import android.widget.Toast;
import androidx.annotation.NonNull;
import androidx.core.app.ActivityCompat;
import javax.microedition.khronos.egl.EGLConfig;
import javax.microedition.khronos.opengles.GL10;

/**
 * A Google Cardboard VR NDK sample application.
 *
 * <p>This is the main Activity for the sample application. It initializes a GLSurfaceView to allow
 * rendering.
 */
// TODO(b/184737638): Remove decorator once the AndroidX migration is completed.
@SuppressWarnings("deprecation")
public class VrActivity extends AppCompatActivity implements PopupMenu.OnMenuItemClickListener {
  static {
    System.loadLibrary("cardboard_jni");
  }

  private static final String TAG = VrActivity.class.getSimpleName();

  // Permission request codes
  private static final int PERMISSIONS_REQUEST_CODE = 2;

  // Opaque native pointer to the native CardboardApp instance.
  // This object is owned by the VrActivity instance and passed to the native methods.
  private long nativeApp;

  private GLSurfaceView glView;
  private ExperiencePlayer experience;
  private TextureFactory textures;

  @SuppressLint("ClickableViewAccessibility")
  @Override
  public void onCreate(Bundle savedInstance) {
    super.onCreate(savedInstance);

    nativeApp = nativeOnCreate();
    String path = getIntent().getStringExtra("bundle");
    File bundleDir = path != null ? new File(path) : new File(getExternalFilesDir(null), "bundle");
    textures = new TextureFactory(bundleDir);
    experience =
        new ExperiencePlayer(
            bundleDir,
            new ExperiencePlayer.Renderer() {
              public void setScene(float[] nodes, String[] keys) { nativeSetScene(nativeApp, nodes, keys); }
              public void moveCamera(float x, float y, float z, int f, float s, int fo) { nativeMoveCamera(nativeApp, x, y, z, f, s, fo); }
              public void setHighlight(int n) { nativeSetHighlight(nativeApp, n); }
              public void setSubtitle(String t) { nativeSetSubtitle(nativeApp, t); }
              public void setGazeEnabled(boolean e) { nativeSetGazeEnabled(nativeApp, e); }
              public void setAnimations(float[] d) { nativeSetAnimations(nativeApp, d); }
              public void setFog(float[] data) { nativeSetFog(nativeApp, data); }
              public void burst(int node) { nativeBurst(nativeApp, node); }
              public void tweenNode(int n, int p, float x, float y, float z, float s) { nativeTweenNode(nativeApp, n, p, x, y, z, s); }
            });
    experience.setOnExit(this::finish);
    if (getIntent().getBooleanExtra("fixed_pose", false)) {
      nativeSetFixedPose(nativeApp, true, getIntent().getFloatExtra("fixed_pose_seconds", 90f));
    }
    if (getIntent().getBooleanExtra("debug_menu", false)) {
      new android.os.Handler(android.os.Looper.getMainLooper())
          .postDelayed(() -> experience.openMenu(0f, 1.6f, 0f, 0f, -1f), 9000);
    }
    if (!experience.load()) {
      Toast.makeText(this, "No bundle found in " + bundleDir, Toast.LENGTH_LONG).show();
    }

    setContentView(R.layout.activity_vr);
    glView = findViewById(R.id.surface_view);
    glView.setEGLContextClientVersion(2);
    glView.setPreserveEGLContextOnPause(false);
    Renderer renderer = new Renderer();
    glView.setRenderer(renderer);
    glView.setRenderMode(GLSurfaceView.RENDERMODE_CONTINUOUSLY);
    // Short tap = the viewer's button (selects what is being looked at). Long press = recentre the content in
    // front of the viewer. Nothing else moves the world.
    glView.setOnTouchListener(
        new View.OnTouchListener() {
          private long downAt;

          @Override
          public boolean onTouch(View view, MotionEvent event) {
            if (event.getAction() == MotionEvent.ACTION_DOWN) {
              downAt = event.getEventTime();
              return true;
            }
            if (event.getAction() == MotionEvent.ACTION_UP) {
              final boolean longPress = event.getEventTime() - downAt > 1200;
              glView.queueEvent(
                  () -> {
                    if (longPress) nativeOnRecenter(nativeApp);
                    else nativeOnTriggerEvent(nativeApp);
                  });
              return true;
            }
            return false;
          }
        });
    // TODO(b/139010241): Avoid that action and status bar are displayed when pressing settings
    // button.
    setImmersiveSticky();
    View decorView = getWindow().getDecorView();
    decorView.setOnSystemUiVisibilityChangeListener(
        (visibility) -> {
          if ((visibility & View.SYSTEM_UI_FLAG_FULLSCREEN) == 0) {
            setImmersiveSticky();
          }
        });

    // Forces screen to max brightness.
    WindowManager.LayoutParams layout = getWindow().getAttributes();
    layout.screenBrightness = 1.f;
    getWindow().setAttributes(layout);

    // Prevents screen from dimming/locking.
    getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
  }

  @Override
  protected void onPause() {
    super.onPause();
    experience.stop();
    nativeOnPause(nativeApp);
    glView.onPause();
  }

  @Override
  protected void onResume() {
    super.onResume();

    // On Android P and below, checks for activity to READ_EXTERNAL_STORAGE. When it is not granted,
    // the application will request them. For Android Q and above, READ_EXTERNAL_STORAGE is optional
    // and scoped storage will be used instead. If it is provided (but not checked) and there are
    // device parameters saved in external storage those will be migrated to scoped storage.
    if (VERSION.SDK_INT < VERSION_CODES.Q && !isReadExternalStorageEnabled()) {
      requestPermissions();
      return;
    }

    glView.onResume();
    nativeOnResume(nativeApp);
    experience.stop();
    if (experience.load()) experience.start(getIntent().getIntExtra("scene", 0));
  }

  @Override
  protected void onDestroy() {
    super.onDestroy();
    nativeOnDestroy(nativeApp);
    nativeApp = 0;
  }

  @Override
  public void onWindowFocusChanged(boolean hasFocus) {
    super.onWindowFocusChanged(hasFocus);
    if (hasFocus) {
      setImmersiveSticky();
    }
  }

  private class Renderer implements GLSurfaceView.Renderer {
    @Override
    public void onSurfaceCreated(GL10 gl10, EGLConfig eglConfig) {
      nativeOnSurfaceCreated(nativeApp);
    }

    @Override
    public void onSurfaceChanged(GL10 gl10, int width, int height) {
      nativeSetScreenParams(nativeApp, width, height);
    }

    @Override
    public void onDrawFrame(GL10 gl10) {
      nativeOnDrawFrame(nativeApp);
    }
  }

  /** Callback for when close button is pressed. */
  public void closeSample(View view) {
    Log.d(TAG, "Leaving VR sample");
    finish();
  }

  /** Callback for when settings_menu button is pressed. */
  public void showSettings(View view) {
    PopupMenu popup = new PopupMenu(this, view);
    MenuInflater inflater = popup.getMenuInflater();
    inflater.inflate(R.menu.settings_menu, popup.getMenu());
    popup.setOnMenuItemClickListener(this);
    popup.show();
  }

  @Override
  public boolean onMenuItemClick(MenuItem item) {
    if (item.getItemId() == R.id.switch_viewer) {
      nativeSwitchViewer(nativeApp);
      return true;
    }
    return false;
  }

  /**
   * Checks for READ_EXTERNAL_STORAGE permission.
   *
   * @return whether the READ_EXTERNAL_STORAGE is already granted.
   */
  private boolean isReadExternalStorageEnabled() {
    return ActivityCompat.checkSelfPermission(this, Manifest.permission.READ_EXTERNAL_STORAGE)
        == PackageManager.PERMISSION_GRANTED;
  }

  /** Handles the requests for activity permission to READ_EXTERNAL_STORAGE. */
  private void requestPermissions() {
    final String[] permissions = new String[] {Manifest.permission.READ_EXTERNAL_STORAGE};
    ActivityCompat.requestPermissions(this, permissions, PERMISSIONS_REQUEST_CODE);
  }

  /**
   * Callback for the result from requesting permissions.
   *
   * <p>When READ_EXTERNAL_STORAGE permission is not granted, the settings view will be launched
   * with a toast explaining why it is required.
   */
  @Override
  public void onRequestPermissionsResult(
      int requestCode, @NonNull String[] permissions, @NonNull int[] grantResults) {
    super.onRequestPermissionsResult(requestCode, permissions, grantResults);
    if (!isReadExternalStorageEnabled()) {
      Toast.makeText(this, R.string.read_storage_permission, Toast.LENGTH_LONG).show();
      if (!ActivityCompat.shouldShowRequestPermissionRationale(
          this, Manifest.permission.READ_EXTERNAL_STORAGE)) {
        // Permission denied with checking "Do not ask again". Note that in Android R "Do not ask
        // again" is not available anymore.
        launchPermissionsSettings();
      }
      finish();
    }
  }

  private void launchPermissionsSettings() {
    Intent intent = new Intent();
    intent.setAction(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
    intent.setData(Uri.fromParts("package", getPackageName(), null));
    startActivity(intent);
  }

  private void setImmersiveSticky() {
    getWindow()
        .getDecorView()
        .setSystemUiVisibility(
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
  }

  /** Called from native on the GL thread. Returns {glTextureId, width, height} or null. */
  public int[] loadTexture(String key) {
    return textures.create(key);
  }

  /** Called from native on the GL thread when the viewer looks straight down for a moment. */
  public void onLookDown(float rx, float ry, float rz, float hx, float hz) {
    experience.openMenu(rx, ry, rz, hx, hz);
  }

  /** Called from native: absolute path of a bundle model (key is "model:<relative path>"). */
  public String modelPath(String key) {
    return experience.dir().getAbsolutePath() + "/" + key.substring(6);
  }

  /** Called from native on the GL thread when the user fixes gaze on a pickable node. */
  public void onGaze(int pickId) {
    experience.onGaze(pickId);
  }

  private native long nativeOnCreate();
  private native void nativeSetScene(long app, float[] nodes, String[] textureKeys);
  private native void nativeMoveCamera(long app, float x, float y, float z, int focus, float seconds, int follow);
  private native void nativeSetHighlight(long app, int node);
  private native void nativeSetSubtitle(long app, String text);
  private native void nativeSetGazeEnabled(long app, boolean enabled);
  private native void nativeSetFixedPose(long app, boolean on, float seconds);
  private native void nativeSetAnimations(long app, float[] data);
  private native void nativeSetFog(long app, float[] data);
  private native void nativeBurst(long app, int node);
  private native void nativeTweenNode(long app, int node, int prop, float x, float y, float z, float seconds);

  private native void nativeOnDestroy(long nativeApp);

  private native void nativeOnSurfaceCreated(long nativeApp);

  private native void nativeOnDrawFrame(long nativeApp);

  private native void nativeOnTriggerEvent(long nativeApp);
  private native void nativeOnRecenter(long nativeApp);

  private native void nativeOnPause(long nativeApp);

  private native void nativeOnResume(long nativeApp);

  private native void nativeSetScreenParams(long nativeApp, int width, int height);

  private native void nativeSwitchViewer(long nativeApp);
}
