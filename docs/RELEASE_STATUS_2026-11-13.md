# MATRIX production release status

**Snapshot:** 2026-11-13  
**Repository:** https://github.com/ayaanraza2008/matrix-ai  
**Default branch:** `main`  
**Integrated candidate:** [PR #72](https://github.com/ayaanraza2008/matrix-ai/pull/72), merged as `f472e0e9f164c0f15bfc2a72b3288f073109f704`  
**Exact candidate tree:** `7b822ccb14c6c1b1a122b9476847d0a49629ce87`

## What is complete in the repository

PR #72 consolidated the Telegram, autonomy, notifications, integrations, Android hardening and dependency-refresh work onto current main. It was squash-merged after exact-head CI passed.

The recorded GitHub Actions run [#43424775584](https://github.com/ayaanraza2008/matrix-ai/actions/runs/43424775584) completed successfully. All seven jobs passed:

- Windows Python 3.11 and 3.12 full test/lint/secret-scan/byte-compile/CLI checks
- Ubuntu Python 3.11 and 3.12 full test/lint/secret-scan/byte-compile/CLI checks
- Android debug and unsigned release APK assembly
- Astryx frontend build
- Blocking dependency audit and CycloneDX SBOM generation

The run published `matrix-python-sbom` and `matrix-android-apks` artifacts. The APK artifact expires 2026-11-20; the SBOM artifact expires 2026-12-13. The release APK is unsigned and is not distributable.

Integrated repository capabilities include:
- Private Tailscale Serve and current-user Windows logon startup (opt-in)
- Owner-paired private Telegram chat, persistent update cursor, idempotent task creation, shared rate limits and emergency stop
- Persistent goals and dependency DAGs, pause/resume/cancel, criteria assessment and owner confirmation
- Scheduled jobs, quiet hours, notification inbox/preferences/digest and optional signed HTTPS health webhooks
- Optional local faster-whisper/Piper speech engines
- Android Keystore token storage, explicit local session clearing and release HTTPS-only policy
- GitHub, YouTube, LinkedIn, Instagram and WhatsApp integration adapters with policy/approval safeguards
- Engineering Coach, Vercel Skills and optional add-on catalogue

## Production certification: still blocked by owner-environment evidence

The source and CI integration are complete; the following cannot be honestly marked complete from GitHub-hosted CI alone.

| Gate | Required evidence | State |
|---|---|---|
| Windows install/startup | Fresh install on target PC; logon startup; stop/start; reboot recovery; upgrade and rollback | Pending target PC |
| Private remote access | Tailscale Serve works only for authorized tailnet devices; ACL isolation and revoke test | Pending owner tailnet |
| Telegram | Fresh bot token stored locally; owner pairing; send/receive; /status; /stop; restart/replay; unauthorized chat rejected | Pending live bot |
| Local voice | Install voice extra and local model/voice; real mic/speaker; mute, stop, barge-in and no-audio-persistence test | Pending target hardware |
| Android | Install debug build; physical pairing/reconnect; session clear and server revocation; network checks | Pending handset |
| Android distribution | Owner-controlled signing key, signed release build, signature verification and secure key custody | Pending owner signing |
| Provider integrations | OAuth/least-privilege setup and read/write smoke tests per enabled provider | Pending owner accounts |
| WhatsApp inbound | Public HTTPS callback, HMAC verification, sender allowlist and replay test | Pending public endpoint + Meta app |
| Operations | Backup/restore rehearsal, audit retention, monitoring, incident recovery and rollback rehearsal | Pending deployment |
| Security review | Review residual DNS-rebinding/TOCTOU risk in public web fetch, data-at-rest protection and release supply-chain posture | Follow-up required |

## Security follow-up applied

The public web research fetch is now disabled by default in both `MatrixConfig` and `.env.example`. Explicitly setting `MATRIX_ALLOW_WEB_FETCH=true` opts back in; do not do so in production until connection-level DNS/IP pinning closes the documented DNS-rebinding/TOCTOU gap. Regression tests cover the secure default and explicit opt-in. The change is committed to `main`; the merged PR #72 CI result predates this follow-up, so a fresh push-triggered CI result must be checked before certifying this change.

## Safety invariants

- HIGH/CRITICAL writes require exact owner approval; Telegram cannot approve them.
- Emergency stop remains authoritative.
- Do not expose the API through public port forwarding or Tailscale Funnel. Use private Tailscale Serve with restrictive ACLs.
- Provider credentials belong in the local environment/OS credential manager; never commit them or paste them into chat.
- Social/provider writes are not enabled merely because adapters exist. Each account, scope and live action needs owner setup and acceptance.
- Telegram task creation is idempotent by update ID. Network delivery and external side effects are at-least-once, not exactly-once.
- Android release artifacts remain unsigned until the owner supplies and controls the signing key.
- Optional NVIDIA PersonaPlex/Nemotron setup scripts do not download model weights, provision a GPU or activate runtime adapters.

## Optional follow-up, not a release blocker

[PR #10](https://github.com/ayaanraza2008/matrix-ai/pull/10) adds a separate Windows SAPI speech-output option and browser voice refinements. The integrated main branch already has optional local Whisper/Piper voice and browser push-to-talk/transcription. PR #10 is based on an older snapshot and has merge conflicts; do not merge it as-is. Rebase and review it only if Windows SAPI is specifically required.

See [`docs/PRODUCTION_ACCEPTANCE_RUNBOOK.md`](PRODUCTION_ACCEPTANCE_RUNBOOK.md) for the exact owner-device validation sequence and evidence requirements.

## Release decision

**Repository integration: complete. Exact-head CI: passed. Production certification: not complete.**

Do not label MATRIX production-ready until the target Windows PC, tailnet, Telegram bot, local audio devices, Android handset, provider accounts and operational recovery procedures have passed the acceptance gates above. This is a concrete, bounded release checklist—not a claim that the assistant is already installed, connected or operating autonomously on the owner's devices.
