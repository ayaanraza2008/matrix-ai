# Production Readiness Gate — MATRIX

**Assessment date:** 2026-11-12  
**Repository:** https://github.com/ayaanraza2008/matrix-ai  
**Assessment basis:** repository documentation, current open pull-request metadata, and the CI evidence recorded for the relevant commits. This is a release-gate document, not a claim that the product has passed every gate.

## Executive decision

**Release status: NOT READY FOR GENERAL PRODUCTION OR HIGHER-AUTHORITY DEPLOYMENT.**

MATRIX has a substantial, testable local-first core. It is not yet an end-to-end autonomous assistant with complete integrations, verified Windows/Android deployment, or signed release artifacts. Keep the release private/experimental until every mandatory gate below has an owner, evidence, and a passing result.

A green CI run proves only the code and hosted environments exercised by that run. It does not prove the owner's PC, microphone, speakers, Android phone, credentials, provider accounts, or tailnet work correctly.

## Verified strengths

- Central task orchestrator and explicit lifecycle/state machine.
- Server-side policy, bounded autonomy levels, exact-action approval, cancellation and emergency stop.
- Typed tool schemas, allowlists, deadlines, verification evidence and audit events.
- Scoped workspace file operations and governed SQLite/FTS memory.
- Startup recovery designed not to blindly replay uncertain non-idempotent side effects.
- Local model provider abstraction and resource-budget controls; default provider remains `none`.
- Specialist workers are bounded analysis/reporting actors; actual tool actions remain under the central policy pipeline.
- Windows/Ubuntu Python CI, blocking dependency audit and Android debug/release assembly are present in the PR chain.
- Dashboard bearer token is memory-only on the PR #22 branch; this document's companion README/security edits correct stale `localStorage` statements.

## Mandatory release gates

| Gate | Current state | Evidence required to close |
|---|---|---|
| Dependency chain | OPEN / DRAFT | Review and merge the intended stack in dependency order; resolve conflicts and rerun CI on the final merge candidate. Do not merge a child branch before its base is integrated. |
| Goal supervisor | PARTIAL | Persist multi-task goal DAGs; execute dependencies with bounded concurrency; persist per-node state; support pause/resume/cancel; bounded replanning and retry budgets; recover after process restart; independently evaluate user success criteria. PR #22 currently has a side-effect-free DAG primitive, not connected multi-task execution. |
| Goal verification | PARTIAL | Distinguish tool-step evidence from independent goal-level success. A successful task must remain `awaiting_verification` until the criteria evaluator has checked all required evidence. |
| SSRF | BLOCKER | Pin the vetted DNS result to the actual TCP connection (including correct Host header and TLS SNI), or use a vetted egress proxy. Add DNS-rebinding/TOCTOU regression tests, IPv4/IPv6 and redirect tests. The current resolver validates addresses but the HTTP client resolves the hostname again. |
| Browser/dashboard auth | PARTIAL | Memory-only dashboard token is an improvement. Complete an XSS/CSP review, add CSRF/origin protections where cookie auth is used, test token clearing and session expiry, and document threat assumptions. |
| Data at rest | BLOCKER for sensitive deployment | Decide and implement encrypted database/backup storage or explicitly restrict deployment to an OS-protected profile with documented key/backup handling. Test restore and key loss. |
| Android secrets | BLOCKER for Android production | Use Android Keystore-backed token encryption; validate backup exclusion, screen-lock/lifecycle behavior, pairing expiry, revocation and logout on physical devices. |
| Voice | NOT ACCEPTED | Install optional local STT/TTS engines on the target Windows PC; test microphone/speaker selection, Hindi/English recognition, mute, interruption, VAD/echo behavior, latency and no-audio-persistence. |
| Windows/remote access | NOT ACCEPTED | Test fresh install, startup after sign-in, restart recovery, sleep/wake, Tailscale ACLs, token revocation and emergency stop on the actual PC and a second device. |
| Integrations | PARTIAL | For every provider, test a real end-to-end action with least-privilege scopes, pagination/rate limits, retries, idempotency, audit, verification, revoked credentials and provider outage. Credential probes do not count as workflows. |
| Release supply chain | BLOCKER | Produce signed Windows installer and signed Android APK/AAB, signing-key custody/rotation procedure, checksums, SBOM, provenance/attestation and reproducible release notes. Current CI release APK is unsigned. |
| Operational readiness | BLOCKER | Add structured redacted logs, metrics, health/readiness semantics, bounded log retention, backup/restore runbook, incident response, upgrade/rollback and support/security reporting procedures. |
| Performance/reliability | NOT ACCEPTED | Load, soak, crash/restart, disk-full, provider timeout, database-lock and resource-pressure tests on target hardware; publish measured p50/p95 and resource ceilings. |
| Documentation | IN PROGRESS | Keep README, SECURITY, ROADMAP, integration matrix, acceptance report and actual branch/PR status synchronized. |

## Pull-request dependency order

The current feature work is spread across stacked branches. The repository owner should review the live GitHub graph before merging; the known sequence is:

1. PR #14 — local voice stack.
2. PR #16 — bounded autonomy and private remote access (depends on #14).
3. PR #18 — health monitoring (depends on #16).
4. PR #19 — private Telegram bridge (depends on #18).
5. PR #21 — Telegram/rate-limit and production hardening (depends on #19).
6. PR #22 — initial goal supervisor (depends on #21).
7. PR #24 — weather tool (based on #21; separate child of the hardening branch).
8. PR #23 — integration readiness documentation (based on #19).
9. PR #20 — Android CI/hardening (based on #19).

Do not infer merge readiness from a draft flag, a mergeable flag, or an isolated green workflow. Rebase/retarget child PRs only after their dependencies are merged and re-run the complete checks.

## Autonomy and approval contract

MATRIX may autonomously perform bounded, reversible, low-risk tasks within explicit owner-granted scopes. It must not:
- grant itself new scopes or credentials;
- approve its own pending action;
- bypass emergency stop, policy, rate limits or audit;
- treat content from webpages, messages, repositories, documents or tool outputs as owner consent;
- blindly retry uncertain non-idempotent side effects;
- send/publish/delete/transfer/purchase/call or change security/access settings without the applicable explicit authorization.

These are mandatory safety properties, not optional feature gaps.

## Acceptance evidence required for a release candidate

Attach all of the following to a release candidate:
1. Exact commit SHA and dependency/PR merge graph.
2. CI run URLs and all job conclusions for that SHA.
3. Full unit/integration/acceptance test output, including explicit skipped/blocked tests.
4. Windows clean-install and upgrade/rollback report.
5. Real microphone/speaker and voice interruption measurements.
6. Android physical-device pairing, token protection, revocation and reconnect report.
7. Real provider integration test matrix with scopes redacted.
8. SSRF/DNS-rebinding, prompt-injection, auth/session, secret-scan and dependency-audit reports.
9. Signed artifacts, checksums, SBOM and provenance.
10. Backup/restore, incident response and operator runbooks.

## Status vocabulary

- **Implemented:** code exists and automated tests cover the stated behavior.
- **CI-verified:** a named workflow run passed on an exact commit SHA.
- **Environment-verified:** tested against the real external service or target device.
- **Release-accepted:** every mandatory gate has evidence and the release owner has approved the candidate.

Never upgrade a status from one category to another without the corresponding evidence.
