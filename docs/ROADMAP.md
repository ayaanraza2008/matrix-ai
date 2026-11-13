# Roadmap

Phases follow the blueprint. A phase is only **complete** when its exit criteria are met with tests and a
reproducible demonstration — not when code exists.

| Phase | Deliverables | Exit criteria | Current status (2026-11-13) |
|---|---|---|---|
| 0. Discovery | Repo inventory, baseline report, integration status | Reproducible baseline + prioritized gaps | **Complete** |
| 1. Stabilize core | API, state machine, tool registry, policy, cancellation, audit | Core tests pass; no unguarded execution | **Implemented; CI passed** |
| 2. Local brain | Model adapter, manager, structured tools, benchmark harness | Local chat + tool routing within RAM budget | **Partial** — adapters and benchmark command exist; target PC model installation/benchmark pending |
| 3. Voice | STT, TTS, wake word, VAD, interruption, mute | Voice E2E on target PC | **Partial** — optional faster-whisper/Piper and push-to-talk transcription integrated; real audio, device selection, wake word, VAD and streaming pending |
| 4. Vision | Permission flow, OCR, gestures, image analysis | On-demand capture with visible state | **Partial** — session/policy and OCR adapter; Tesseract and target-camera acceptance pending |
| 5. Desktop automation | Scoped Windows tools, browser, files, monitoring | Typed, allowlisted, verified, audited calls | **Partial** — core file/app/shell/system tools integrated; full UI automation/Playwright workflows pending |
| 6. Android companion | Compose UI, pairing, chat, camera, notifications | Connects securely, survives reconnect | **Scaffold + CI build** — debug and unsigned release assemble; physical-device validation and owner signing pending |
| 7. Memory & proactive | Governed memory, scheduler, goals, monitoring | Retention/deletion/limits tested | **Core implemented; ops acceptance pending** — persistent goal DAGs, owner review, scheduler, pause/resume/cancel, quiet hours and notification routing integrated |
| 8. Integrations | GitHub, Google, Telegram, Home Assistant, telephony | Each adapter has setup + health + failure handling | **Partial/provider-dependent** — Telegram and guarded GitHub/YouTube/LinkedIn/Instagram/WhatsApp adapters integrated; OAuth, credentials and live provider acceptance remain per account |
| 9. Polish & release | JARVIS UI, docs, packaging, CI, E2E acceptance | Release candidate meets acceptance suite | **CI-validated candidate; not certified** — PR #72 merged and exact-head CI passed; installer, signed Android release, target-device tests and operational rehearsal pending |

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
- [x] Add timezone-aware quiet hours, a bounded persistent in-app notification inbox and owner read acknowledgements.
- [x] Add owner-controlled recovery/daily-digest preferences and a once-daily aggregate summary.
- [x] Add signed HTTPS webhook health notifications with exact-host allowlisting, HMAC signatures and per-channel retry tracking.
- [ ] Add further push providers and validate live webhook/Telegram delivery in the owner environment.

### M7 — Dependency-aware task plans

- [x] Validate planner step dependency indexes, unknown references, duplicates and cycles.
- [x] Preserve zero-based `depends_on` relationships through the planner parser.
- [x] Execute only dependency-ready steps, including prerequisites that appear later in the plan.
- [x] Block transitive descendants after a failed or unverified prerequisite.
- [x] Continue independent branches after a step failure; final task outcome remains PARTIAL/FAILED when any required branch did not complete.
- [ ] Add persistent multi-task goal DAG records, bounded goal-level replanning and crash recovery.
- [ ] Add independent semantic evaluation for owner-authored success criteria; until then goals remain `awaiting_verification` rather than being silently marked verified.
- Acceptance: hermetic dependency tests and full Windows/Ubuntu CI pass; production acceptance additionally requires restart/recovery and real integration tests.

### M8 — Persistent executable goal DAGs

- [x] Persist bounded goal nodes and dependency edges in SQLite.
- [x] Add authenticated plan, start, graph-status and cancellation APIs.
- [x] Dispatch up to four dependency-ready nodes per goal through the normal task orchestrator.
- [x] Continue independent branches and block descendants of failed nodes.
- [x] Recover orphaned dispatch claims and resume durable goal graphs after startup.
- [x] Resume graph reconciliation after a node approval decision.
- [x] Add a separate read-only model criteria-assessment pass; keep goals awaiting owner review when no model/evidence is available.
- [ ] Add explicit owner-triggered replan with a no-side-effect replay gate and bounded replan count.
- [ ] Test restart recovery, pending-approval resume and cancellation during a live node on Windows and Linux.
- [ ] Run the full CI matrix on the final PR head and complete end-to-end acceptance.
