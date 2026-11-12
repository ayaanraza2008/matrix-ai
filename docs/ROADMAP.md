# Roadmap

Phases follow the blueprint. A phase is only **complete** when its exit criteria are met with tests and a
reproducible demonstration — not when code exists.

| Phase | Deliverables | Exit criteria | Status |
|---|---|---|---|
| 0. Discovery | Repo inventory, baseline report, integration status | Reproducible baseline + prioritized gaps | **Complete** — `docs/BASELINE_REPORT.md` (empty repo, fresh build) |
| 1. Stabilize core | API, state machine, tool registry, policy, cancellation, audit | Core tests pass; no unguarded execution | **Complete** — A01/A03–A08/A16/A17/A22/A23 pass |
| 2. Local brain | Model adapter, manager, structured tools, benchmark harness | Local chat + tool routing within RAM budget | **Partial** — adapters/manager done; no benchmark harness or model configured on this PC |
| 3. Voice | STT, TTS, wake word, VAD, interruption, mute | Voice E2E on target PC | **Partial** — mute/barge-in/PTT control done and tested; engines blocked |
| 4. Vision | Permission flow, OCR, gestures, image analysis | On-demand capture with visible state | **Partial** — sessions + OCR adapter; Tesseract/MediaPipe/camera missing |
| 5. Desktop automation | Scoped Windows tools, browser, files, monitoring | Typed, allowlisted, verified, audited calls | **Partial** — files/apps/shell/system done; pywinauto + Playwright pending |
| 6. Android companion | Compose UI, pairing, chat/voice, camera, notifications | Connects securely, survives reconnect | **Scaffold** — Kotlin app structure + API contract committed; not built |
| 7. Memory & proactive | Governed memory, scheduler, goals, monitoring | Retention/deletion/limits tested | **Partial** — memory + jobs done; quiet hours/alert routing pending |
| 8. Integrations | GitHub, Google, Telegram, Home Assistant, telephony | Each adapter has setup + health + failure handling | **Partial** — adapters and status implemented; credentials unconfigured |
| 9. Polish & release | JARVIS UI, docs, packaging, CI, E2E acceptance | Release candidate meets acceptance suite | **Partial** — UI/docs/CI/acceptance done; installer and signed build pending |

## Next milestones

### M0.1 — Startup recovery (Phase 1 reliability follow-up)
- Reconcile persisted tasks before background workers start.
- Requeue untouched RECEIVED tasks; preserve valid pending approvals; resume only already-approved exact actions.
- Mark interrupted tool calls and active steps as failed with an explicit uncertain-outcome message; never blindly replay them.
- Acceptance: startup recovery tests cover requeue, pending approval preservation, approved-action resume and in-flight no-replay.

### M1 — Model on the 8 GB PC (Phase 2 exit)
- Pull `qwen2.5:0.5b-instruct-q4_K_M`; keep ≤ 2 500 MB budget.
- Add a benchmark script: tool-selection accuracy, JSON validity, Hindi/English prompts, p50 latency, peak RSS.
- Acceptance: ≥ 90 % valid JSON plans on the MATRIX prompt set; peak RSS within budget; `/api/models` reports `working`.

### M2 — Voice engines (Phase 3 exit)
- faster-whisper `tiny.en`/`base` + Piper voice; push-to-talk path first.
- Wire `VoiceSessionManager.transcribe/speak` to real engines; keep mute check before engine use.
- Acceptance: A11/A12 run against real audio; barge-in stops playback ≤ 300 ms; no raw audio persisted.

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
