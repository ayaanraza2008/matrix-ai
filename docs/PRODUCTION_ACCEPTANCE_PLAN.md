# Production acceptance plan

**Decision: NOT READY for production release until every mandatory gate below has current, reproducible evidence.**

This plan turns the current roadmap and acceptance report into an ordered release process. Code present on a branch, a passing unit test, or a green CI run is not by itself proof that a feature works on the owner's Windows PC, Android handset, Telegram bot, or third-party accounts.

## Release principles

- Keep the core free-first and local-first. Disclose any provider quota, account requirement, or hosting cost.
- Do not merge stacked feature branches out of dependency order.
- Never claim an integration is live because an adapter, credential probe, or mock test exists.
- All external/destructive/high-risk actions remain behind exact-action approval.
- Do not retry uncertain non-idempotent side effects after a crash.
- A task is complete only when its verifier records evidence. A goal is verified only when its explicit success criteria are independently checked and the owner confirmation policy is satisfied.
- Keep secrets out of source control, issue comments, logs, screenshots, and chat.

## Ordered work plan

### Gate 1 — Establish a clean, current baseline

- [ ] Record the exact `main` commit SHA and the open PR list.
- [ ] Build the dependency graph for stacked PRs; identify each PR's base branch and required predecessor.
- [ ] Run the full Windows and Ubuntu CI matrix on the integrated candidate, not just isolated PR heads.
- [ ] Run lint, secret scan, byte-compile, full tests, dependency audit, and SBOM generation.
- [ ] Reconcile README, roadmap, capability matrix, and acceptance report so their dates and claims agree.
- [ ] Resolve all blocking security findings before release; document accepted residual risks.

**Evidence:** commit SHA, PR dependency map, CI run links, test counts, audit output, and an updated acceptance report.

### Gate 2 — Complete the autonomy engine

- [ ] Integrate per-task dependency-aware plan execution.
- [ ] Integrate persistent multi-task goal DAG planning, bounded scheduling, pause/resume, cancellation, and restart recovery.
- [ ] Enforce bounded attempts, deadlines, concurrency, and resource budgets.
- [ ] Ensure failed prerequisites block descendants while independent branches may continue.
- [ ] Preserve idempotency and refuse blind replay after uncertain side effects.
- [ ] Independently evaluate owner-defined success criteria using persisted evidence; fail closed on missing, malformed, unavailable, or low-confidence verification.
- [ ] Keep owner confirmation explicit where required; audit all goal lifecycle changes.
- [ ] Exercise restart/crash recovery with active, waiting, approved, expired, failed, and partially completed tasks.

**Evidence:** integration tests on the merged candidate, restart tests, goal-DAG traces, persisted evidence, and negative tests proving unsafe work is blocked.

### Gate 3 — Make Telegram a real remote control centre

- [ ] Configure the bot token and exact owner chat/sender allowlist in the owner's local environment; never commit or paste secrets into repository artifacts.
- [ ] Verify private-owner authentication and rejection of every non-owner sender.
- [ ] Verify command submission reaches the real MATRIX orchestrator and returns a task ID.
- [ ] Deliver progress, approval requests, final results, failures, and recovery notices to the correct private chat.
- [ ] Verify approval messages bind to the exact action, arguments, expiry, and task; Telegram text must not bypass the policy broker.
- [ ] Verify emergency stop works while rate-limited and blocks scheduled and interactive tool execution.
- [ ] Verify duplicate Telegram updates do not duplicate task execution.
- [ ] Test bot restart, MATRIX restart, network loss, reconnect, rate limiting, and oversized/malformed updates.
- [ ] Document supported commands and honest limitations.

**Evidence:** redacted end-to-end test transcript, test timestamps, task/audit IDs, and explicit pass/fail results on the target Windows PC. Until this gate passes, Telegram must not be described as a verified control centre.

### Gate 4 — Complete third-party integrations

For each provider (GitHub, YouTube, Instagram, LinkedIn, WhatsApp, and any additional requested service):

- [ ] Identify the exact OAuth scopes, permissions, quotas, regional restrictions, and costs.
- [ ] Test account connection and token refresh in the owner's environment.
- [ ] Test read operations against the real account.
- [ ] Test write operations only with explicit owner approval and a safe test target.
- [ ] Verify provider errors, rate limits, expired/revoked credentials, duplicate requests, and audit redaction.
- [ ] Confirm platform terms and API restrictions; do not promise unsupported account-management features.
- [ ] Publish a per-action support matrix: available, read-only, approval-gated write, unavailable, or not tested.

**Evidence:** redacted provider test records and per-action support matrix. An adapter or OAuth probe alone does not pass this gate.

### Gate 5 — Windows, Android, voice, and desktop acceptance

- [ ] Run a clean Windows install from documented instructions.
- [ ] Verify startup, shutdown, restart, upgrade, backup, restore, and uninstall.
- [ ] Build and sign the Android release artifact; test pairing, encrypted token storage, reconnect, revocation, offline behavior, and notifications on a physical handset.
- [ ] Install and test speech-to-text and text-to-speech engines; verify mute, push-to-talk, interruption, and no raw-audio persistence.
- [ ] Install and test OCR dependencies; report skipped tests honestly.
- [ ] Implement and test browser/desktop automation only through typed, allowlisted, approval-aware tools.
- [ ] Measure model quality, latency, RAM, and CPU on the actual target PC; do not assume a model fits based on a configured ceiling.

**Evidence:** installer/APK hashes, device and OS versions, reproducible test logs, measured resource metrics, and known limitations.

### Gate 6 — Security and operations

- [ ] Close the documented DNS rebinding/connect-time gap and re-test redirects, private IPs, DNS changes, and response bounds.
- [ ] Review secret handling, dashboard token storage, local database protection, Android token lifecycle, and backup permissions.
- [ ] Verify network exposure is private by default; document and test the recommended VPN/TLS configuration for remote access.
- [ ] Verify least-privilege scopes, owner authorization, rate limits, audit redaction, emergency stop, and approval expiry.
- [ ] Run dependency vulnerability scanning and generate a CycloneDX SBOM.
- [ ] Define operational health checks, logs, backup cadence, restore procedure, incident response, and rollback.
- [ ] Publish signed release artifacts and checksums.

**Evidence:** security review, threat-model updates, SBOM, scan reports, signed artifact metadata, and restore/rollback drill.

### Gate 7 — Final release decision

- [ ] Run all mandatory gates against one immutable release candidate commit.
- [ ] Re-run the full CI and acceptance suite after the final merge.
- [ ] Mark every acceptance item PASS, FAIL, SKIPPED, or BLOCKED with evidence and owner.
- [ ] Publish release notes, setup guide, integration matrix, security notes, known limitations, and rollback steps.
- [ ] Release only when all mandatory blockers are closed or explicitly accepted by the owner with a documented risk statement.

## Current known baseline (from the 2026-11-12 acceptance report)

The report records 140 passed and 1 skipped automated tests on its tested candidate, plus a successful Windows/Ubuntu CI run on an earlier commit. It also explicitly records that the local model was not configured, speech engines were absent, OCR was skipped, Android physical-device acceptance was pending, integrations were unconfigured, browser automation was not installed, and a signed installer/release was not available.

These are historical observations, not proof of the current `main` state. Re-run them on the release candidate.

## Telegram acceptance: minimum operator test

1. Start MATRIX on the target PC and confirm authenticated health.
2. Send a harmless read-only status request from the configured owner Telegram account.
3. Send a bounded, reversible workspace task and confirm progress plus verified completion.
4. Request a HIGH-risk test action and confirm it pauses for exact approval.
5. Deny the action and prove no side effect occurred.
6. Repeat with approval and verify the exact intended side effect.
7. Trigger emergency stop and prove new work is blocked.
8. Send a command from a non-owner account and prove it is rejected.
9. Restart Telegram bridge and MATRIX; verify no duplicate execution and correct recovery reporting.

Do not perform destructive, financial, public-posting, or account-changing tests on production accounts.

## Release status vocabulary

- **Implemented:** code exists.
- **CI-verified:** automated checks passed on the exact commit/PR head.
- **Environment-verified:** tested in the target Windows/Android/provider environment.
- **Release-accepted:** all required evidence reviewed and accepted.
- **Blocked / Skipped:** a dependency or environment prevented the test; never count it as a pass.

**Current release decision remains NOT READY until the gates are evidenced on a single integrated release candidate.**
