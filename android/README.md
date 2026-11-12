# MATRIX Android companion — scaffold

This module is a **scaffold**, not a shipped app. It was not compiled in the environment that created this
repository because no Android SDK was available there. Read [`../docs/ANDROID.md`](../docs/ANDROID.md) before
using it.

## What it does

- Pair with a MATRIX PC using a single-use code (`matrix pair-code`).
- Send a chat request, poll the last task, and inspect its state/summary.
- Show a pending approval and approve or deny it explicitly.
- Check the device session.

## Build

```powershell
# From android/ in Android Studio (or with a local SDK + Gradle 8.7+)
gradle wrapper --gradle-version 8.7
.\gradlew.bat assembleDebug
```

The debug APK and the unsigned release APK are uploaded as the `matrix-android-apks` artifact by CI (7-day retention). The release APK is not signed and is not for distribution.

Then install:

```powershell
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

For debug emulator networking, enter `http://10.0.2.2:8765` as the PC base URL (cleartext is permitted in the debug build only). Release builds require an HTTPS URL. On a physical device, use the PC's
private-network address (Tailscale recommended) and keep MATRIX bound to a private interface with TLS in
front of it for anything beyond development.

## Security notes

- The token is kept in memory in this scaffold. Before shipping, store it in `EncryptedSharedPreferences`
  and add biometric/device-lock gating.
- Cleartext HTTP is allowed only by `src/debug/AndroidManifest.xml`; the main/release manifest explicitly disables it. Release builds should use HTTPS (for example, Tailscale Serve).
- Camera, notifications, offline cache and WorkManager are not implemented yet (Phase 6).
