# MATRIX acceptance report — current main

**Snapshot:** 2026-11-13  
**Integrated source:** PR #72, merged to `main` as `f472e0e9f164c0f15bfc2a72b3288f073109f704`  
**Exact candidate tree validated:** `7b822ccb14c6c1b1a122b9476847d0a49629ce87`

## Automated CI evidence

GitHub Actions run [#43424775584](https://github.com/ayaanraza2008/matrix-ai/actions/runs/43424775584) completed with **7/7 jobs successful** on the exact PR #72 candidate tree:

| Job | Result |
|---|---|
| Windows Python 3.11 full test/lint/secret scan/byte-compile/CLI | PASS |
| Windows Python 3.12 full test/lint/secret scan/byte-compile/CLI | PASS |
| Ubuntu Python 3.11 full test/lint/secret scan/byte-compile/CLI | PASS |
| Ubuntu Python 3.12 full test/lint/secret scan/byte-compile/CLI | PASS |
| Android debug + unsigned release assemble | PASS |
| Astryx frontend build | PASS |
| Blocking pip-audit + CycloneDX SBOM | PASS |

Artifacts published by that run:
- `matrix-python-sbom` (retention through 2026-12-13)
- `matrix-android-apks` (retention through 2026-11-20; release APK is unsigned)

This is strong repository/CI evidence. It is not a substitute for a fresh installation or physical-device testing.

## Integrated implementation coverage

- Orchestrator state machine, typed tools, policy engine, exact-action approvals, cancellation, emergency stop and audit trail.
- Persistent SQLite task/goal state, dependency-aware goal DAGs, bounded scheduler, pause/resume/cancel, criteria assessment and owner confirmation.
- Private Telegram chat with sender/chat pairing, restart-safe update watermark, task idempotency, rate limiting and emergency stop.
- Private Tailscale Serve and current-user Windows logon startup, both opt-in.
- Local optional faster-whisper/Piper STT/TTS; voice-note transcription remains bounded and follows the normal task policy.
- Android companion scaffold with encrypted Android Keystore token storage, local session clearing and release HTTPS-only networking.
- Optional GitHub, YouTube, LinkedIn, Instagram and WhatsApp integrations with approval/webhook boundaries.
- Notification inbox, owner preferences, daily digest, quiet hours and optional signed HTTPS health webhook.
- Engineering Coach, Vercel Skills and optional add-on catalogue.

## Acceptance gates not proven by hosted CI

| Gate | What must be demonstrated | State |
|---|---|---|
| Fresh Windows installation | Install from clean environment, run doctor/tests, first-run token, service start/stop | Target PC required |
| Windows startup/recovery | Current-user logon task, reboot, restart, DB recovery, upgrade and rollback | Target PC required |
| Tailscale privacy | Serve is tailnet-only; restrictive ACLs; unauthorized device denied; revoke access | Owner tailnet required |
| Telegram | New owner-controlled bot token; private pairing; receive/reply; /status; /stop; restart/replay; unauthorized sender denied | Live bot required |
| Voice | Install optional extra and model assets; real microphone/speaker; mute/stop/barge-in; verify no raw audio persists | Target audio hardware required |
| Android | Install debug APK; pair; reconnect; clear session; revoke device server-side; test HTTPS/network policy | Physical handset required |
| Android release signing | Owner-controlled keystore, signed APK, signature verification, secure backup of signing key | Owner signing key required |
| Provider OAuth | Least-privilege consent and live read/write tests for each provider actually enabled | Owner accounts and credentials required |
| WhatsApp inbound | Public HTTPS callback, Meta verification, HMAC, sender allowlist and replay test | Public endpoint and Meta app required |
| Operations | Backup/restore, audit retention, monitoring, incident response and rollback rehearsal | Deployment environment required |
| Security | Close or explicitly accept documented DNS-rebinding/TOCTOU ([tracked in issue #74](https://github.com/ayaanraza2008/matrix-ai/issues/74)) risk; review local DB encryption, token storage and supply-chain posture | Security sign-off required |

## Important limitations

- Local model inference is optional and not configured by this repository CI. Install a compatible local runtime/model on the target PC and benchmark against its memory budget.
- OCR requires Tesseract and the vision extra.
- Wake word, VAD, echo cancellation, streaming partial transcripts and full WebRTC/SIP/PSTN calling are not claimed complete.
- Android CI assembles the app but does not sign it or validate a physical handset.
- Provider adapters do not imply that accounts are connected or that live publishing/messaging works.
- No public Tailscale Funnel or API port forwarding is configured or recommended.
- Telegram task creation is idempotent by update ID, but Telegram replies and external side effects remain at-least-once.

## Security follow-up: public web fetch

The DNS-rebinding/TOCTOU gap was identified in `web.fetch_public`: the hostname was validated before the request, but the HTTP client resolved it again when opening the connection. `MATRIX_ALLOW_WEB_FETCH` remains **false by default**. [PR #75](https://github.com/ayaanraza2008/matrix-ai/pull/75) implements connection-level pinning to validated numeric IPv4/IPv6 addresses, preserves the original hostname for TLS SNI/certificate verification and HTTP Host, verifies the connected peer, and adds deterministic tests. Exact PR-head CI run [#43432576244](https://github.com/ayaanraza2008/matrix-ai/actions/runs/43432576244) passed **7/7 jobs**. PR #75 remains open for independent security review and merge. Keep public web fetch disabled until review accepts the implementation and it is merged. Track the gate in [issue #74](https://github.com/ayaanraza2008/matrix-ai/issues/74).
See [`docs/PRODUCTION_ACCEPTANCE_RUNBOOK.md`](PRODUCTION_ACCEPTANCE_RUNBOOK.md) for the exact owner-device validation sequence and evidence requirements.

## Release decision

**Repository integration and exact-head CI: PASS. Production certification: BLOCKED pending owner-environment evidence and security sign-off.**

This report deliberately separates automated repository checks from evidence that can only be collected on the owner's Windows PC, tailnet, Telegram bot, provider accounts and Android handset.
