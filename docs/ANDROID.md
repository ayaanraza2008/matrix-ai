# Android Companion — Scaffold Status

> **Honest status:** the Android app is a **scaffold**. The project initially had no Android build job; draft PR #21 adds
> a GitHub Actions job that assembles debug and release variants. That CI result and a physical-device acceptance run
> are still required before this can be called a working companion.

## What is in `android/`

```text
android/
├── settings.gradle.kts
├── build.gradle.kts
├── gradle.properties
└── app/
    ├── build.gradle.kts
    ├── proguard-rules.pro
    └── src/main/
        ├── AndroidManifest.xml      main/release manifest (cleartext disabled)
        ├── debug/AndroidManifest.xml (debug-only cleartext override)
        ├── java/com/ayaanraza/matrix/
        │   ├── MainActivity.kt      Compose UI: connect, chat, task state, approvals
        │   ├── MatrixClient.kt      Tiny HTTP client (HttpURLConnection, no extra deps)
        │   └── UiState.kt           Small view-model state holder
        └── res/values/strings.xml
```

## Security design (intended)

- Pairing uses a single-use 6-digit code (`matrix pair-code`), exchanged for a device token.
- The scaffold keeps the paired token in transient UI memory only; it is not written to saved instance state.
  EncryptedSharedPreferences/Android Keystore integration is still required before shipping.
- Each request sends `Authorization: Bearer <device token>`.
- Device revocation on the PC invalidates the token immediately (`auth.verify_token`).
- No secrets are embedded in the APK.
- Cleartext HTTP is permitted only in debug builds; the main/release manifest sets `usesCleartextTraffic=false`.
  Release builds must use HTTPS (for example, a private Tailscale Serve URL).
- Camera capture must be explicit, permission-gated and stopped when the screen/session ends.

## API contract used by the app

| Purpose | Endpoint |
|---|---|
| Health (no auth) | `GET /api/health` |
| Pair | `POST /api/auth/pair` `{code, device_name, platform}` |
| Verify session | `GET /api/auth/me` |
| Chat | `POST /api/chat` `{message}` → task |
| Poll task | `GET /api/tasks/{id}` |
| Approve / deny | `POST /api/tasks/{id}/approvals/{aid}/approve|deny` |
| Cancel | `POST /api/tasks/{id}/cancel` |
| Devices | `GET /api/devices`, `POST /api/devices/{id}/revoke` |

A task is terminal when `state` is one of `COMPLETE`, `PARTIAL`, `FAILED`, `CANCELLED`. Approvals appear in
`task.approvals` with `status: "pending"`.

## Build steps (on a machine with Android Studio)

1. Open the `android/` folder in Android Studio (Giraffe/Koala or newer).
2. Let Gradle sync (compileSdk 34, minSdk 26, Kotlin 2.0.21 (Compose compiler plugin), Compose BOM 2024.06.00).
3. Point the app at your PC:
   - Debug emulator: `http://10.0.2.2:8765` (debug build only).
   - Debug physical device: `http://<PC LAN IP>:8765` on a trusted LAN only.
   - Release build: use an HTTPS endpoint such as the private Tailscale Serve URL; plaintext HTTP is blocked.
   - For non-loopback binds, set `MATRIX_HOST` accordingly and understand the TLS caveat in `docs/SETUP.md`.
4. Build: `./gradlew assembleDebug` (Windows: `gradlew.bat assembleDebug`).
5. Install and pair with a code from `matrix pair-code`.

## Not implemented yet

- Gradle wrapper jar (run `gradle wrapper` or let Android Studio generate it).
- CameraX capture + upload, notifications/FCM, speech, offline Room cache, WorkManager jobs.
- QR pairing (code entry only), biometric unlock, certificate pinning.
- APK signing/release pipeline.

These are tracked in `docs/ROADMAP.md` (Phase 6). Nothing here should be described as working until it is
built and tested on a device.
