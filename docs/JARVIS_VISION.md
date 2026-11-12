# MATRIX: JARVIS-Class Vision and Engineering Blueprint

> Goal: evolve MATRIX from a secure assistant core into a capable, multimodal personal operator for Windows and Android. The ambition is a practical system that can understand, plan, perceive, communicate, and carry out approved work—not a claim of fictional sentience or unlimited autonomy.

## 1. Product north star

MATRIX should feel like one coherent assistant across text, voice, desktop, browser, mobile, and connected services.

A user should be able to say or type:
- "Summarize these documents and prepare a report in my workspace."
- "Find why my PC is slowing down, show the evidence, and suggest a fix."
- "Open the project, inspect the failing tests, and propose a patch."
- "Remind me when the backup is stale."
- "Turn off the office lights." (only after a configured Home Assistant integration and the appropriate approval policy)
- "Read this screen and explain what I should do next."

The system must distinguish **understanding**, **planning**, **execution**, and **verified completion**. A plausible answer is not proof that an action occurred.

## 2. What exists today

The current repository already contains a useful base:
- One task orchestrator and explicit lifecycle state machine.
- A server-side policy engine with autonomy levels, risk classes, exact-action approvals, cancellation, and an emergency stop.
- Typed tool adapters with schemas, allowlists, timeouts, verification hooks, and an audit trail.
- Scoped workspace file operations, application launch, allowlisted commands, public-page retrieval, and system telemetry.
- SQLite + FTS5 governed memory with deletion and retention support.
- Local-model provider abstraction, hardware-aware resource manager, and a read-only model benchmark.
- Optional CPU/INT8 faster-whisper transcription with browser push-to-talk.
- Browser transcript handoff to Chat and opt-in browser/system speech for final replies.
- Vision session controls and an OCR adapter.
- Scheduler and integration adapters, plus an Android client scaffold.

These are foundations, not evidence that all target capabilities are already implemented. The capability matrix and acceptance report remain the source of truth for implementation status.

## 3. Target experience

### A. Conversation and voice
- Continuous conversation with clear session start/stop and visible listening state.
- Push-to-talk as the reliable default; optional local VAD and wake word only after explicit opt-in.
- Streaming speech recognition with partial transcripts and language detection for English and Hindi.
- Natural spoken replies with selectable local voices, pause/resume, stop, and barge-in.
- Conversation context that survives navigation and can be resumed after reconnect.
- Spoken output must never read credentials, private tokens, hidden prompts, or approval secrets.

### B. Brain and planning
- One primary local model at a time, selected by measured memory and latency—not marketing claims.
- A model router that can use a fast small model for routine work and an explicitly enabled stronger provider for complex work.
- Structured plans validated against tool schemas before execution.
- Deterministic routing for known tasks, model fallback for ambiguous requests, and clear clarification when intent is underspecified.
- Multi-step plans with dependencies, checkpoints, budgets, cancellation, and verified outcomes.
- A skills catalogue: each skill has a manifest, inputs, outputs, required scopes, risk class, timeouts, tests, and version.

### C. Memory and personalization
- Separate short-term conversation context from long-term user-approved memory.
- Store preferences, project facts, useful summaries, and user-created routines with provenance and confidence.
- Search with SQLite FTS first; add local embeddings only if benchmarked retrieval quality justifies the extra memory and CPU cost.
- Provide inspect, edit, export, expiry, and delete controls.
- Never store credentials, raw microphone audio, or camera frames by default.

### D. Vision and screen understanding
- On-demand screenshot capture with a visible capture indicator and a clear stop control.
- OCR for text, window titles, and accessibility metadata.
- Optional image understanding through a local model when a measured device profile can support it.
- Screen context must be treated as untrusted input; page text or on-screen instructions cannot override MATRIX policy.
- Camera use must be per-session, explicit, and off by default.

### E. Windows desktop and browser operation
- Window enumeration and focused-window inspection.
- UI controls through a typed Windows automation adapter (candidate: pywinauto).
- Browser workflows through an isolated Playwright adapter with site allowlists and explicit login/session boundaries.
- File, app, browser, and system operations composed into a single plan.
- Every action needs a typed schema, narrow scope, timeout, independent verification, and audit event.
- Clicking, typing, deleting, installing software, sending messages, publishing, or changing security settings must be classified by risk; consequential actions require exact approval.

### F. Android companion
- Secure one-time pairing, device identity, revocation, and short-lived sessions.
- Chat, task status, approvals, push notifications, and voice controls.
- Camera upload only after a visible permission action.
- Clear online/offline state and safe reconnect without replaying uncertain side effects.
- Android remains a client of the central policy/orchestration service, not an independent authority.

### G. Proactive assistant and routines
- Reminders, recurring checks, local system monitoring, project health checks, and scheduled reports.
- Quiet hours, per-job budgets, cooldowns, deduplication, and alert severity.
- Notifications through configured channels such as Android push or Telegram.
- Read-only monitoring can be autonomous within a user-approved scope.
- Any outbound communication, spending, public publishing, or irreversible change requires explicit action-level approval.

### H. Integrations and smart environment
- GitHub, calendar, email, messaging, Home Assistant, and local device adapters.
- Least-privilege credentials stored outside the repository and never exposed to model prompts.
- Health check, capability list, failure mode, rate limit, audit, and revoke flow for every integration.
- No integration is advertised as available until credentials and a real end-to-end test pass.

### I. Interface and identity
- A polished desktop dashboard with chat, voice state, active plans, approvals, tool traces, memory, and system health.
- A compact always-on-top HUD is optional, not required for the core assistant.
- Real state only: animations and status indicators must reflect actual API state.
- Accessibility, keyboard navigation, reduced-motion support, and responsive layout are first-class requirements.

## 4. Reference architecture

Keep the existing single-brain design. Extend it with adapters rather than parallel agents that bypass policy.

```text
Windows dashboard / CLI / Android companion
                  |
        Authenticated API gateway
                  |
      Conversation + session service
                  |
      Central orchestrator / planner
           /          |          \
      Memory       Policy      Scheduler
           \          |          /
             Typed tool registry
             /       |        \
          Desktop  Browser  Integrations
             \       |        /
              Verification layer
                     |
          Redacted audit + SQLite
                     |
       Local model / STT / TTS / vision
```

### Non-negotiable contracts
1. **Models propose; policy decides.** A model cannot grant itself scopes, change risk, approve an action, or write directly to the operating system.
2. **Tools are typed.** No arbitrary shell string, eval, unrestricted Python execution, or free-form browser command.
3. **Approvals bind exact actions.** Bind tool name + normalized arguments + task/session + expiry; changing any argument invalidates approval.
4. **Verification is independent.** A tool can report success only with evidence such as process state, read-back hash, expected page state, or API confirmation.
5. **Untrusted inputs stay tainted.** Web pages, documents, OCR, screenshots, and third-party tool output cannot inject instructions into policy.
6. **Cancellation is immediate and honest.** Never retry a non-idempotent operation after an uncertain result.
7. **Privacy is explicit.** Microphone, camera, screen capture, remote access, and cloud inference each have separate visible consent and stop controls.
8. **The emergency stop dominates.** It blocks new work and cancels active eligible tasks.

## 5. Resource strategy for the target PC

The build should work CPU-first and must not require CUDA or a discrete NVIDIA GPU.

- Detect physical RAM, available RAM, CPU cores, OS, and GPU metadata at startup.
- Preserve the existing model memory budget until actual measurements justify a change.
- Benchmark compact quantized models (start with the repository's 0.5B profile, then test 1.5B; only consider larger models if measured RSS and latency leave headroom).
- Use one primary model at a time, bounded context, idle unload, and short outputs for tool planning.
- Keep faster-whisper on CPU/INT8 with a small model until English/Hindi latency and accuracy are measured.
- Prefer browser/OS speech or a small local TTS engine over heavyweight speech generation on low-resource hardware.
- Make all heavyweight capabilities optional extras; startup and basic chat must still work without them.
- Do not infer that an integrated AMD GPU will accelerate a specific model. Hardware acceleration is an optional measured optimization, never a requirement.

## 6. Delivery roadmap and exit criteria

| Milestone | Scope | Deliverables | Exit criteria |
|---|---|---|---|
| J0 — Voice conversation | STT-to-chat and spoken replies | Push-to-talk, transcript handoff, browser speech, stop/barge-in | English + Hindi real-device test; no audio persistence; playback cancels reliably |
| J1 — Reliable local brain | Model profile and conversation continuity | Local model configured, context manager, memory retrieval, plan repair | Benchmark passes schema + tool-selection thresholds within the measured RAM budget |
| J2 — Windows operator | UI inspection and safe controls | Window listing, accessibility tree, typed click/type/key actions | Every action is scoped, approved where needed, verified, audited, and cancellation-tested |
| J3 — Browser operator | Research and web workflows | Playwright browser profile, site policy, login boundary, page-state verification | Injection tests, domain allowlist tests, no credential exposure, replay-safe execution |
| J4 — Visual intelligence | Screenshot and document perception | Capture session, OCR, optional local image model, UI grounding | Explicit permission, visible indicator, no frame persistence by default, repeatable test set |
| J5 — Proactive engine | Routines and monitoring | Quiet hours, limits, deduplication, notifications | Scheduled jobs recover safely; no duplicate side effects after restart |
| J6 — Android companion | Mobile control surface | Pairing, chat, approval push, voice, camera | Physical-device test, revoke test, offline/reconnect test |
| J7 — Connected environment | Personal services and smart home | GitHub, calendar, messaging, Home Assistant adapters | Credentials scoped, each integration health-tested, consequential writes approved |
| J8 — Release quality | Installer, updates, diagnostics | Windows installer, backup/restore, signed builds, doctor | Fresh-install acceptance suite, upgrade/rollback rehearsal, security review |

A milestone is complete only when its exit criteria are met. Adding a dependency or a UI button does not make a capability complete.

## 7. Initial implementation priorities

1. Finish and validate the complete voice round trip on the physical Windows machine.
2. Configure and benchmark the smallest useful local model; do not install a large model blindly.
3. Build the Windows UI automation adapter on top of the existing typed tool + policy contracts.
4. Add Playwright only after site allowlists, browser isolation, and untrusted-page handling are tested.
5. Complete memory/context continuity and scheduled monitoring with quiet hours.
6. Turn the Android scaffold into a real paired control client.
7. Package the product only after core acceptance tests pass.

## 8. Safety boundary

MATRIX is an assistant under user authority, not an unbounded autonomous actor.

- Never activate the microphone or camera silently.
- Never bypass OS permissions, MFA, or website access controls.
- Never exfiltrate browser cookies, credentials, tokens, or private data.
- Never send, publish, purchase, delete, install, or modify security settings without the required exact approval.
- Do not execute commands copied from an untrusted page or document.
- Do not change the safety policy or grant new permissions as part of a normal task.
- Provide a clear preview of the exact action before approval.
- Preserve a user-visible action history and a global stop control.

## 9. Acceptance checklist for every new capability

- [ ] Typed input schema rejects extra or malformed fields.
- [ ] Tool is registered with the narrowest permission scope.
- [ ] Risk is determined by server policy, not model output.
- [ ] HIGH/CRITICAL or tainted actions require exact approval.
- [ ] Network, file, process, and time limits are enforced.
- [ ] Cancellation and timeout behavior are tested.
- [ ] Verification evidence is independent of the model's claim.
- [ ] Audit records are redacted and contain no raw media or credentials.
- [ ] Restart/reconnect behavior cannot replay uncertain side effects.
- [ ] Documentation marks the capability done, partial, scaffolded, or blocked honestly.
- [ ] CI passes on Windows and Linux; hardware-dependent tests are clearly separated.

## 10. Definition of success

The practical target is a dependable personal operator that can converse naturally, understand permitted context, plan multi-step work, use the desktop and browser safely, remember approved information, act across devices, and prove what it accomplished.

"Beyond JARVIS" should mean better privacy controls, transparent permissions, verifiable execution, modularity, and reliability—not pretending that MATRIX is conscious, omniscient, or able to control every device.
