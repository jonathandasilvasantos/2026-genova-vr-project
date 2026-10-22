# Visual-effects regression checks

These are deterministic software checks. They make no provider calls and do not change the
three published cases or the article's empirical observations.

Run `sh tests/vfx/run.sh` from the Android directory with `JAVA_HOME` and `JSON_JAR` set.
`JSON_JAR` must contain working `org.json` classes; the Android SDK's `android.jar` contains
stubs and cannot run the JSON checks. The locally cached Robolectric Android image supplies
executable, uninstrumented `org.json` classes without starting a device.

Checks cover all authoring presets, macro hierarchy, forward light targets, packed values,
emitter/light/particle/fog budgets, one-shot and looping lifecycles, signed gravity, drag,
visibility, emission shapes, finite positions, light modulation, and fog falloff. Native
logic runs with AddressSanitizer and UndefinedBehaviorSanitizer.

`EffectsCatalog.java` mirrors `EMITTER_DEFAULTS`, `PRESETS`, `LIGHT_DEFAULTS`, `LIGHT_PRESETS`,
and `MACROS` in `nodes/effects.py`. Keep them synchronized when changing presets.
The Java/native node stride is 64 floats; indices 0--31 retain the prior node fields and
32--57 carry effect parameters. It is an internal bridge, not a manifest-format change.

`shader_test.cc` optionally checks translated GLES shader compilation/linking using a macOS
OpenGL context (`clang++ -std=c++17 -Wno-deprecated-declarations -framework OpenGL ...`).
It removes ES precision syntax for desktop GLSL 1.20. This does not validate Android GPU
behavior. In the restricted October 7 environment, no OpenGL pixel format was available;
that check remains unexecuted, not passed.

## Device verification

Build with `./gradlew :app:assembleDebug`, install the resulting debug APK, and stage this
directory's `manifest.json` as a private bundle using the README's `bundle_path` shortcut.
The fixture has no audio, model, or texture assets and requires no generation.
Verify both eyes show fire, a spotlight on the box, a visible beam, and fog; an explosion
fires at two seconds and again on later steps. Hide/show and group movement should affect
the fire and explosion without drawing sphere proxies. Captions and text labels stay clear.
Inspect logcat for shader-link errors and GL errors, then force-stop the debug app.

Device verification is pending: the sandbox blocked ADB's socket listener. Neither an APK
installation nor an Android screenshot is claimed by the local checks.
