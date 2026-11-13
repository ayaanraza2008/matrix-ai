# MATRIX release status — 2026-11-13

## Current engineering state

MATRIX has gained several real implementation layers, but it is **not yet a production release**. Changes are stacked as draft pull requests and have not been merged to the default branch. A green CI run proves repository tests/build checks, not live provider access or operation on the owner's Windows/Android devices.

## Feature and validation matrix

| Area | Implementation in current PR stack | Automated validation | Live acceptance |
|---|---|---|---|
| Task dependency execution | Planner preserves `depends_on`; executor gates on prerequisites and blocks descendants after failure | PR #29: all 6 CI jobs passed | Not yet |
| Persistent goal DAGs | Persistent nodes, bounded dispatch, recovery, verification state | PR #31: all 6 CI jobs passed | Not yet |
| Goal controls | Owner pause/resume/cancel; paused goals stay paused after restart | PR #35: all 6 CI jobs passed | Not yet |
| YouTube | Official OAuth; channel/video/comment reads; approval-gated comment replies, metadata updates, private uploads and scheduling | PR #33: all 6 CI jobs passed | OAuth consent and channel smoke test required |
| GitHub | Account/repository/issues/PR reads; approval-gated issue creation/comments | PR #35: all 6 CI jobs passed | Fine-grained token and repository smoke test required |
| Weather | Free Open-Meteo current conditions and 1–7 day forecast | PR #35: all 6 CI jobs passed | No key; network availability still applies |
| LinkedIn | Approval-gated text posts through official Posts API | PR #38: all 6 CI jobs passed | Developer app permission and token required |
| Instagram | Comment listing/replies and approval-gated image publishing | PR #38 and #39: all 6 CI jobs passed | Professional account, Meta app permissions, token and public image URL required |
| WhatsApp outbound | Approval-gated text and approved template messages | PR #38: all 6 CI jobs passed; PR #41 latest-head CI pending | Business Cloud API token, phone-number ID, opt-in and approved templates required |
| WhatsApp inbound | HMAC-signed webhook, GET verification, sender allowlist, message-ID deduplication and ordinary owner-task intake | PR #40: all 6 CI jobs passed | Public HTTPS endpoint and Meta app configuration required |
| Telegram | Private two-way text bot, bounded polling and voice-note input | Included in PR #31 CI | Bot token and owner chat ID required |
| Local voice | Self-hosted Whisper/Piper path | Earlier PR #14/PR #31 CI coverage | Real microphone/speaker, driver, model download and interruption tests required |
| Android | Debug and release APK assembly | CI Android build | Physical device, pairing, signing and permission acceptance required |

## Approval and permission model

- Read-only scopes are enabled selectively by default.
- Provider writes are non-idempotent and require exact owner approval; high-risk/critical writes are not blindly retried.
- Optional write scopes are explicitly configured through `MATRIX_EXTRA_SCOPES`.
- Provider tokens are read from local environment or the OS credential manager; do not paste tokens into chat or commit them.
- Incoming WhatsApp messages are accepted only from `MATRIX_WHATSAPP_ALLOWED_SENDERS`, authenticated using the Meta app secret, and routed through the ordinary task policy. They do not auto-approve actions.

## Still-open production blockers

1. Merge the stacked draft PRs in dependency order only after final CI and code review.
2. Complete owner-controlled OAuth/token setup and live smoke tests for YouTube, GitHub, LinkedIn, Instagram and WhatsApp.
3. Deploy WhatsApp inbound webhook behind a deliberately configured public HTTPS endpoint. A private Tailscale-only service cannot receive Meta callbacks.
4. Test Telegram text and voice notes with the real bot, private chat and audio devices.
5. Test Windows microphone capture, Whisper transcription, Piper playback, cancellation, device sleep/restart and low-memory operation.
6. Install and test the Android app on physical devices; configure production signing and secure backup/recovery.
7. Complete installer, upgrade/rollback, backup/restore, logs/telemetry, incident recovery and production security review.
8. Implement or explicitly defer remaining platform capabilities: Instagram Reels/Stories/DMs, LinkedIn media uploads/analytics, WhatsApp media/interactive messages and inbound media handling, YouTube Analytics/playlists/thumbnails/livestreams.
9. Complete persistent multi-process goal leases and bounded goal-level replanning/recovery. Current goal supervisor does not claim unrestricted self-modification or unlimited autonomous recovery.
10. Decide and document data-at-rest encryption, retention and deletion. Local SQLite content relies on host OS disk/account protections unless separately encrypted.

## Cost and provider constraints

The core is free-first and can use local models, but “completely free” cannot be guaranteed. Provider API quotas, app reviews, business messaging, public HTTPS hosting/tunnels, cloud compute, telephony and storage can incur charges or require approval. WhatsApp business messaging pricing and Meta/LinkedIn/Google usage policies remain authoritative.

## Release decision

**Status: NOT READY FOR PRODUCTION.**

CI-passing PRs are still draft and unmerged. No real personal accounts, live webhook, local microphone, Android handset or public production endpoint were connected or tested in this work session. Do not describe MATRIX as 100% autonomous or production-ready until the owner-environment acceptance gates above are evidenced.


## Follow-up engineering changes (2026-11-13)

The current consolidated branch adds two important correctness fixes after the previously recorded CI run:

- Telegram startup no longer drains pending updates. Its last successfully handled update offset is persisted in SQLite, and a failed update retains the offset for retry. This avoids silently discarding a legitimate owner message during MATRIX restart.
- Telegram delivery is currently **at-least-once**, not exactly-once. A crash after task creation but before cursor persistence may replay an update; task creation idempotency keyed to Telegram update ID is still required.
- Goal model assessment is advisory only. Goals remain `awaiting_verification` until the authenticated owner submits `POST /api/goals/{goal_id}/confirm`; owner identity, timestamp, reason and decision are persisted in schema v6 and audited.
- Regression tests and migration coverage were added for these paths.

These changes were subsequently split into the restart-safe Telegram cursor work in PR #60 and consolidated into the current-main integration candidates. PR #65 is the preferred private Telegram / remote access / Android / voice foundation, but it remains a draft and is currently behind current `main`; PR #60 remains stacked on the YouTube integration branch. Neither should be treated as released until rebased, reviewed and tested on its exact final head. No live Telegram, device, OAuth or webhook acceptance is claimed.

## Live integration snapshot (2026-11-13 09:55 UTC)

- PR #61 (Vercel Skills rebased onto current main) is merged.
- PR #66 (Engineering Coach session adapters) is merged.
- PR #65 is the preferred foundation for private Telegram, Tailscale access, Android hardening, local voice and persistent goals. It is a draft, non-mergeable, and behind current main; the exact intervening main commits and overlapping files must be reconciled.
- PR #60 adds persisted Telegram update offsets and replay idempotency, but is stacked on the YouTube integration branch rather than current main.
- PR #58/#62 and #63 are older, non-mergeable integration candidates. Prefer the #65 line for the Telegram/Android foundation and port only independently reviewed missing changes from other branches.
- PR #55/#64 contain notification inbox and signed webhook work on a separate stack. Do not assume webhook support is in PR #65; integrate it only after reviewing the exact diff and dependencies.
- PR #67 is a temporary review candidate created from an older stacked branch; it is not a replacement for PR #65 and is not a release candidate while merge conflicts remain.

The current release remains **NOT READY**. Required owner-environment evidence still includes real private Telegram pairing/chat/stop/restart/replay, Windows startup and recovery, microphone/speaker voice, Tailscale ACL isolation, physical Android pairing/network checks and signing, plus any provider OAuth/webhook smoke tests. Keep all high/critical writes behind exact owner approval and retain the emergency stop.
