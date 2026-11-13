# MATRIX acceptance report — current main

**Snapshot:** 2026-11-13  
**Latest reported production-gate merge:** PR #79, commit `51191bf`; main CI reported **7/7 jobs green**.  
**Certification verdict: NOT CERTIFIED.** No acceptance failures are currently reported, but six mandatory real-world gates remain blocked or unverified. Repository CI and implementation status are not substitutes for live acceptance.

## Current merged implementation evidence

The following work is reported merged to `main`:

- PR #72 — integrated release candidate.
- PR #75 — DNS-pinned public web-fetch transport; `MATRIX_ALLOW_WEB_FETCH` remains disabled pending independent review.
- PR #76 — opt-in Windows SAPI, speech preflight, push-to-talk, mute/stop and barge-in behavior.
- PR #77 — reproducible release packaging, SBOM and Android APK build workflow; release APK remains unsigned.
- PR #78 — Telegram preflight, backup integrity hardening and independent security review package.
- PR #79 — failed-upgrade recovery regression test proving database integrity/task data preservation and idempotent migration retry after simulated interruption.

The latest session report states main CI is 7/7 green on `51191bf`. Earlier CI run IDs and PR #72-era reports below are historical evidence for those exact trees only; they must not be presented as the latest-main run.

## Mandatory acceptance gates

| Gate | Current state | Evidence required to close |
|---|---|---|
| 1. Independent security review | **BLOCKED** | Qualified reviewer who did not author the DNS-pinning change records identity (or explicit anonymity), date, decision and findings using [the review package](SECURITY_REVIEW_WEB_FETCH.md). Owner's COMMENTED review and agent review are not independent approval. Keep `MATRIX_ALLOW_WEB_FETCH=false`. |
| 2. Telegram live acceptance | **BLOCKED** | Owner configures `MATRIX_TELEGRAM_BOT_TOKEN` and `MATRIX_TELEGRAM_CHAT_ID` in the local secret store/environment, runs `matrix telegram-preflight --send-test`, then completes runbook §4 on the live bot. Never commit or post credentials. |
| 3. Windows host acceptance | **BLOCKED** | Clean install, doctor/tests, startup at current-user logon, restart/sleep/wake/network recovery, backup restore, upgrade/rollback, local-model benchmark if model-driven autonomy is in scope, and microphone/speaker/STT/TTS/push-to-talk/barge-in tests on the intended PC. |
| 4. Tailscale remote access | **BLOCKED** | Owner enrolls only approved devices, configures restrictive ACLs, validates private Serve + MATRIX authentication, verifies unauthorized-device denial and revocation, and confirms no Funnel/public port-forwarding. |
| 5. Android acceptance and signing | **BLOCKED** | Install debug APK on a physical handset; validate HTTPS pairing, harmless task flow, reconnect/restart, local session clearing and server-side revocation. Owner signs release APK with a protected key, verifies signature and tests the signed build. |
| 6. Provider connections | **BLOCKED** | For each provider actually intended for deployment, owner grants least-privilege OAuth/credentials and completes authenticated read-only smoke tests, expiry/revocation and rate-limit checks. WhatsApp inbound additionally requires HTTPS callback verification, HMAC, sender allowlist and replay protection. |

## Recovery, packaging and CI

- Backup corruption/truncation and secret-boundary protections have in-repository regression coverage (PR #78).
- Failed-upgrade migration recovery is now regression-tested (PR #79). This proves the simulated code path in CI, not a live restore/rollback rehearsal on the deployment PC.
- Release packaging and Android APK assembly are cloud-validated; the Android release APK is intentionally unsigned until owner-controlled signing.
- The latest reported main CI result is 7/7 green on `51191bf`. Consult the repository Actions page for the authoritative run and job details: https://github.com/ayaanraza2008/matrix-ai/actions

## Operational rules

- Keep public web fetch disabled until independent security sign-off is recorded. The implementation is merged; the remaining blocker is independent review, not another merge.
- Do not treat an adapter as connected merely because its code exists. Track each provider as **IMPLEMENTED**, **CONNECTED**, **TESTED**, **PRODUCTION READY**, or **BLOCKED**.
- Do not send public posts/messages, perform destructive actions, or approve HIGH/CRITICAL actions through Telegram without the required explicit owner approval.
- Never place bot tokens, OAuth secrets/refresh tokens, signing keys, private messages or personal data in GitHub issues, commits, logs or ChatGPT.
- Record each live test with timestamp, environment/app versions, sanitized evidence and exact result.

## Release decision

**Repository implementation and reported CI: PASS. Production certification: NOT CERTIFIED.**

Certification remains blocked until every mandatory gate required by the intended deployment has real evidence. Optional providers may remain disabled and explicitly deferred; they must not be described as production-ready. See [the owner-environment acceptance runbook](PRODUCTION_ACCEPTANCE_RUNBOOK.md) and [tracking issue #73](https://github.com/ayaanraza2008/matrix-ai/issues/73).
