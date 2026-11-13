# Roadmap

Phases follow the blueprint. A phase is only **complete** when its exit criteria are met with tests and a
reproducible demonstration — not when code exists.

| Phase | Deliverables | Exit criteria | Status |
|---|---|---|---|
| 0. Discovery | Repo inventory, baseline report, integration status | Reproducible baseline + prioritized gaps | **Complete** — `docs/BASELINE_REPORT.md` (empty repo, fresh build) |
| 1. Stabilize core | API, state machine, tool registry, policy, cancellation, audit | Core tests pass; no unguarded execution | **Complete** — A01/A03–A08/A16/A17/A22/A23 pass |
| 2. Local brain | Model adapter, manager, structured tools, benchmark harness | Local chat + tool routing within RAM budget | **Partial** — adapters/manager done; no benchmark harness or model configured on this PC |
| 3. Voice | STT, TTS, wake word, VAD, interruption, mute | Voice E2E on target PC | **Partial** — optional local faster-whisper/Piper wired; target audio E2E, wake word, VAD and streaming pending |
| 4. Vision | Permission flow, OCR, gestures, image analysis | On-demand capture with visible state | **Partial** — sessions + OCR adapter; Tesseract/MediaPipe/camera missing |
| 5. Desktop automation | Scoped Windows tools, browser, files, monitoring | Typed, allowlisted, verified, audited calls | **Partial** — files/apps/shell/system done; pywinauto + Playwright pending |
| 6. Android companion | Compose UI, pairing, chat/voice, camera, notifications | Connects securely, survives reconnect | **Scaffold** — Kotlin app structure + API contract committed; debug/release assemble CI added in PR #21; physical-device validation pending |
| 7. Memory & proactive | Governed memory, scheduler, goals, monitoring | Retention/deletion/limits tested | **Partial** — owner-created recurring goals respect the global autonomy ceiling; quiet hours, alert routing and deeper goal lifecycle pending |
| 8. Integrations | GitHub, Google, Telegram, Home Assistant, telephony | Each adapter has setup + health + failure handling | **Partial** — the registry has credential/connectivity probes (GitHub identity, Telegram `getMe`, Home Assistant API ping, Google OAuth-file validation, Open-Meteo reachability, Firebase service-account validation and Twilio account lookup). Telegram also has the two-way chat/health-alert bridge in PRs #18/#19. Full Gmail/Calendar OAuth actions, GitHub issue/PR actions, Home Assistant entity controls, Firebase device registration/delivery and end-to-end telephony call flows remain unfinished; credentials are unconfigured. |
| 9. Polish & release | JARVIS UI, docs, packaging, CI, E2E acceptance | Release candidate meets acceptance suite | **Partial** — docs/CI/acceptance plus opt-in private Tailscale access and Windows logon startup; installer, signed build and physical-device E2E pending |

## Next milestones

### M0.1 — Startup recovery (Phase 1 reliability follow-up)
- Reconcile persisted tasks before background workers start.
- Requeue untouched RECEIVED tasks; preserve valid pending approvals; resume only already-approved exact actions.
- Mark interrupted tool calls and active steps as failed with an explicit uncertain-outcome message; never blindly replay them.
- Acceptance: startup recovery tests cover requeue, pending approval preservation, approved-action resume and in-flight no-replay.

### M1 — Model on the 8 GB PC (Phase 2 exit)
- Pull `qwen2.5:0.5b-instruct-q4_K_M`; keep ≤ 2 500 MB budget.
- [x] Add matrix benchmark-model: schema-valid plan rate, expected tool selection, English/Hindi prompts, p50/p90 latency and sampled RSS; no tool execution or cloud calls.
- [ ] Run the benchmark on the target PC and choose the largest model that stays inside the measured memory budget.
- Acceptance: ≥ 90 % valid JSON plans on the MATRIX prompt set; peak RSS within budget; `/api/models` reports `working`.

### M2 — Voice production validation (Phase 3 exit)
- [x] Wire optional faster-whisper STT and Piper TTS into `VoiceSessionManager` (PR #14).
- [x] Offload CPU-bound STT inference from the FastAPI event loop; keep mute checks before capture/inference.
- [ ] Validate real microphone and speaker devices on the target Windows PC with the optional voice extra installed.
- [ ] Measure barge-in latency (target ≤ 300 ms); add device selection, VAD, echo cancellation and streaming partial transcripts.
- Acceptance: A11/A12 run against real audio, barge-in stops playback ≤ 300 ms, and no raw audio persists.

### M3 — Windows UI + browser automation (Phase 5 exit)
- pywinauto window/control inspection; Playwright for approved sites.
- Every adapter: typed schema, allowlist, timeout, verification, audit; page content tainted.
- Acceptance: A16 extended to UI actions; A18 covers browser-originated injection.

### M4 — Android companion (Phase 6 exit)
- Build the scaffold with Android Studio; pair via code; chat + task status + approvals; CameraX upload.
- Acceptance: A19/A20 on a physical device; offline state visible; revocation immediate.

### M5 — Packaging & release (Phase 9 exit)
- PyInstaller or MSIX, startup shortcut, signed release, `matrix doctor` in the bundle.
- Acceptance: fresh install on the target PC passes A01–A25 (except environment-blocked IDs, reported honestly).


### M6 — Always-available, bounded autonomy

- [x] Add an opt-in per-user Windows logon task with limited privileges.
- [x] Add opt-in Tailscale Serve access that proxies only to loopback and never configures public Funnel.
- [x] Expose authenticated automation and private-access status.
- [x] Honour owner-created job autonomy up to the global MATRIX autonomy ceiling; preserve exact approval for HIGH/CRITICAL.
- [ ] Validate a real Windows restart/sign-in/remote tailnet session and verify revocation/ACL behavior.
- [x] Add persistent database/model/emergency-stop health transitions, with optional Telegram alerts and audit records.
- [x] Persist task-step evidence separately from owner success-criteria verdicts; run a read-only criteria verifier after all task steps are verified, and keep low-confidence/missing evidence in awaiting_verification.
- [ ] Add persistent multi-task goal DAG records, bounded goal-level replanning and crash recovery.
- [ ] Add quiet hours and richer notification routing before broader proactive behavior.

### M7 — Dependency-aware task plans

- [x] Validate planner step dependency indexes, unknown references, duplicates and cycles.
- [x] Preserve zero-based `depends_on` relationships through the planner parser.
- [x] Execute only dependency-ready steps, including prerequisites that appear later in the plan.
- [x] Block transitive descendants after a failed or unverified prerequisite.
- [x] Continue independent branches after a step failure; final task outcome remains PARTIAL/FAILED when any required branch did not complete.
- [ ] Add persistent multi-task goal DAG records, bounded goal-level replanning and crash recovery.
- [ ] Add independent semantic evaluation for owner-authored success criteria; until then goals remain `awaiting_verification` rather than being silently marked verified.
- Acceptance: hermetic dependency tests and full Windows/Ubuntu CI pass; production acceptance additionally requires restart/recovery and real integration tests.

### M8 — YouTube Data API connector

- [x] Add official Google OAuth refresh-token client; keep access tokens in memory only and never log/return credentials.
- [x] Add channel, recent-video and comment read tools.
- [x] Add public comment replies and resumable video uploads as HIGH-risk, owner-approved actions.
- [x] Default uploads to private and enforce approved file roots plus a 512 MiB upload cap.
- [x] Keep write permission opt-in via `MATRIX_EXTRA_SCOPES=youtube.write`.
- [x] Add an integration health check that confirms OAuth can read the authenticated channel.
- [ ] Complete owner OAuth consent, real channel smoke tests, quota checks and upload processing acceptance.

### M9 — Durable Telegram update handling

- [x] Persist the last successfully handled Telegram update ID in SQLite.
- [x] Resume long polling from the saved offset instead of draining pending updates at startup.
- [x] Advance the offset only after successful handling; failed updates remain retryable.
- [x] Add a unique task source-update key so replayed messages cannot create duplicate tasks.
- [x] Document the at-least-once delivery boundary and Telegram sendMessage duplicate-notification caveat.
- [ ] Verify real Telegram delivery across process restarts with the owner's configured bot.
