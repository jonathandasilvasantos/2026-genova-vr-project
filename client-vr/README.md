# Genova VR Android player

A Google Cardboard application that downloads Genova VR experiences from a
server and plays them as guided tours in a Cardboard viewer.

## A. Install the ready-made APK

The file `apk/genova-vr-debug.apk` is a debug build made from the source in this
folder. Debug builds are not signed for the Play Store.

1. Copy the APK to the phone, or transfer it with `adb`:
   ```sh
   adb install -r apk/genova-vr-debug.apk
   ```
2. If the phone asks, allow installation from this source.
3. Open **Genova VR**. It lists the experiences from the server configured in the
   build (see section C) and offers to download them.

Requirements: Android 8.0 (API 26) or newer. A Google Cardboard viewer or a
compatible headset is recommended. Without a viewer, the app still shows the
scene on the screen.

## B. Build from source

### B.1 Tools

- JDK 17
- Android SDK with platform 35 and build-tools (install through Android Studio or `sdkmanager`)
- `git`, to fetch the pinned third-party SDK sources

### B.2 Fetch the pinned dependencies

From the repository root (the folder that contains `tools/` and `client-vr/`):

```sh
sh tools/fetch_vendor.sh
```

This clones the Cardboard SDK and tinygltf at fixed revisions into `vendor/`.
The script refuses to overwrite a checkout that differs from those revisions.

### B.3 Configure the SDK path

Create `client-vr/local.properties` (it is not versioned) with the path to your SDK:

```properties
sdk.dir=/path/to/Android/sdk
```

### B.4 Build and install

```sh
cd client-vr
./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## C. Choose the server

The player fetches experiences from a server URL. The default is set at build
time in `app/build.gradle`:

```groovy
buildConfigField "String", "SERVER_URL", '"http://192.168.0.10:8080"'
```

For a local server on the same Wi-Fi network, set it to your computer's address,
for example `http://192.168.0.10:8080`, rebuild, and reinstall. The phone must
reach that address. Use plain HTTP only on a trusted local network.

The app stores the server address in its preferences once it is set, so changing
the build default does not change an installed app that already stored one.
Clear the app data or reinstall to apply a new address.

## D. Developer shortcut

For debugging, the player can start an experience directly:

```sh
adb shell am start -n ai.genova.vr/com.google.cardboard.LauncherActivity --es play <experience-id>
```

## Layout

```
client-vr/
├── app/           Android application (Java, C++ native renderer, Cardboard integration)
├── apk/           Debug APK built from this source
├── build.gradle   Project build files
├── settings.gradle
└── gradlew        Gradle wrapper
```

## Known limitations

- Playback of every scene and quiz path has not been verified on every device.
- Comfort, accessibility and learning effects have not been evaluated.
- Use the debug build for testing only.
