# MATRIX Android companion — scaffold

This module is a **scaffold**, not a shipped app. It has a CI build for debug and unsigned release variants, but it still requires physical-device acceptance before it can be called a production companion. Read [`../docs/ANDROID.md`](../docs/ANDROID.md) before using it.

## What it does

- Pair with a MATRIX PC using a single-use code (`matrix pair-code`).
- Send a chat request, poll the last task, and inspect its state/summary.
- Show a pending approval and approve or deny it explicitly.
- Check the device session and clear the local session explicitly.

## Build

```powershell
# From android/ in Android Studio (or with a local SDK + Gradle 8.7+)
gradle wrapper --gradle-version 8.7
.\\gradlew.bat assembleDebug assembleRelease
```

The debug APK and unsigned release APK are uploaded as the `matrix-android-apks` CI artifact (7-day retention). The release APK is **not signed** and is not for distribution.

Install the debug APK:

```powershell
adb install -r app\\build\\outputs\\apk\\debug\\app-debug.apk
```

For debug emulator networking, enter `http://10.0.2.2:8765` as the PC base URL. The debug network-security configuration permits cleartext only to `10.0.2.2`, `localhost`, and `127.0.0.1`. Physical-device access should use HTTPS (for example, Tailscale Serve) and the PC's private-network address.

## Security notes

- The paired token is held in non-saveable in-memory Compose state and can be cleared with **Clear local session**. Android Keystore-backed encrypted persistence and biometric/device-lock gating are not implemented yet.
- The main/release manifest disables cleartext traffic. The debug-only network-security overlay allows only the emulator/loopback hosts listed above.
- Camera, notifications, offline cache and WorkManager are not implemented yet (Phase 6).
