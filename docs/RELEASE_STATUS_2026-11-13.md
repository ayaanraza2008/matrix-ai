# MATRIX release status — 2026-11-13

## Authoritative repository state

**Production status: NOT CERTIFIED.** The integrated repository candidate is merged to `main`, and exact-head CI passed. This is a repository/CI milestone, not proof of deployment or live operation on the owner's Windows PC, Telegram account, Android handset or provider accounts.

- Integrated release PR: [#72](https://github.com/ayaanraza2008/matrix-ai/pull/72) — merged.
- Merge commit: `f472e0e9f164c0f15bfc2a72b3288f073109f704`.
- Exact candidate CI: [workflow run #43424775584](https://github.com/ayaanraza2008/matrix-ai/actions/runs/43424775584).
- Exact-head jobs: Windows Python 3.11/3.12; Ubuntu Python 3.11/3.12; Android debug + unsigned release assembly; Astryx frontend; blocking pip-audit; CycloneDX SBOM generation/upload. All seven jobs passed.
- PRs #68 and #69 were superseded and closed; PR #72 is the integrated source of truth. Historical candidate references earlier in this document are not current merge instructions.
- PR #10 remains an optional, conflicted Windows SAPI/browser push-to-talk follow-up. It is not required for the integrated core and must not be merged without reconciliation and fresh CI. Core optional voice adapters are already in main.
- PR #36 (NVIDIA PersonaPlex/Nemotron setup documentation/scripts) is merged. It does not download model weights, provision a GPU, or activate a runtime adapter.

## Integrated capabilities

- Private Telegram chat bridge with durable update cursor, idempotent task creation, task recovery/result reporting, rate limits and emergency-stop handling.
- Persistent goal execution, dependency-aware scheduling, owner controls and explicit owner confirmation for goal completion.
- Private Tailscale Serve and Windows current-user startup helpers.
- Android companion scaffold with Android Keystore token storage and strict release network policy.
- Optional local speech adapters, voice session controls and browser voice experience.
- GitHub, YouTube, weather, LinkedIn, Instagram and WhatsApp integration adapters, guarded by scopes and action approvals.
- Authenticated notification inbox, owner preferences, daily digest and optional signed HTTPS health webhook.
- Blocking dependency audit, SBOM publication and multi-platform Python CI.

Capabilities remain conditional on configuration. A provider adapter in the repository does not mean an account is connected or live writes have been tested.

## Remaining production certification gates

These require the owner's target devices, accounts, credentials or operational decisions; repository CI cannot complete them by itself.

1. **Windows installation and recovery:** install from the intended environment; verify current-user sign-in startup, process restart, sleep/wake recovery, upgrade and rollback.
2. **Private remote access:** verify Tailscale Serve from an enrolled remote device, review restrictive tailnet ACLs, test revocation, and confirm no Funnel/public port forwarding.
3. **Telegram:** configure a fresh bot token and numeric private owner chat ID locally; verify Start, private chat, task results, voice-note path, restart/replay, rate limiting and emergency stop. Never put tokens in Git or chat.
4. **Local audio:** install/select an STT/TTS engine and verify microphone capture, English/Hindi recognition as needed, speaker output, interruption, cancellation, latency and RAM on the actual PC.
5. **Android:** install the debug build on a physical handset; verify pairing, reconnect, clear-local-session and server-side revocation over HTTPS; configure protected signing and produce an owner-signed release APK.
6. **Provider accounts:** connect least-privilege GitHub/Google/Meta/LinkedIn credentials and run read-only smoke tests first. Test each write only with explicit owner approval and a harmless target. WhatsApp inbound additionally requires a public HTTPS callback, HMAC secret and sender allowlist.
7. **Operations:** rehearse backup/restore, upgrade/rollback, audit retention, incident recovery and emergency-stop recovery.
8. **Security review:** independently review the merged code and residual risks, especially DNS rebinding/TOCTOU in public web fetching, host compromise, token storage and provider scopes.

## Known technical and product limits

- Telegram update handling provides at-least-once processing with idempotent task creation; reply delivery and external provider side effects are not exactly-once.
- The HTTP fetch SSRF defence validates DNS before requesting, but the HTTP client resolves the hostname again when connecting. DNS-rebinding/TOCTOU remains a documented residual risk; do not describe this as fully mitigated.
- Android release assembly produces an **unsigned** APK. It is not distributable until owner-controlled signing and device acceptance are complete.
- MATRIX requires the host PC to be powered on, the Windows user signed in, network connected and the service running. Tailscale does not make a powered-off PC reachable.
- HIGH/CRITICAL actions still require exact owner approval. Telegram cannot approve them, and MATRIX must not grant itself permissions or bypass emergency stop.
- Social/media capabilities are limited to implemented provider APIs and scopes. Reels/Stories/DMs, LinkedIn media/analytics, WhatsApp media/interactive messages and other absent features are not implied.
- PersonaPlex/Nemotron weights and production runtime are not installed by the setup documentation alone.
- The optional PR #10 Windows SAPI/browser push-to-talk work remains a separate, non-blocking follow-up and is not merged.

## Release decision

**Repository integration: COMPLETE. Exact-head CI: PASS. Production certification: NOT COMPLETE.**

Do not advertise MATRIX as 100% autonomous or production-ready until the owner-environment gates above have reproducible evidence. Keep the emergency stop and exact-action approval requirements enabled throughout acceptance.
