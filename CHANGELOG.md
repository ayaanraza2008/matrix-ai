# Changelog

All notable changes are documented here. Dates use ISO 8601.

## [0.1.0] — 2026-11-12

First public foundation release, built directly against `ayaanraza2008/matrix-ai` (the repository was empty;
see `docs/BASELINE_REPORT.md`).

### Added

- **Core orchestration** — explicit task state machine (`RECEIVED → UNDERSTAND → POLICY_CHECK → PLAN →
  TOOL_SELECTION → [APPROVAL] → EXECUTE → VERIFY → COMPLETE/PARTIAL/FAILED/CANCELLED`), durable task
  records, deadlines, cancellation and resumable approvals.
- **Policy engine** — risk classes, autonomy L0–L4, per-task permission scopes, exact approval binding
  (`sha256(tool + canonical args)` + TTL), untrusted-content risk escalation, emergency stop.
- **Tool registry** — 14 typed adapters (system telemetry, scoped files, allowlisted apps and commands,
  memory, public web fetch with SSRF guard, OCR), each with timeout, idempotency declaration, audit and
  verification evidence.
- **Model runtime** — provider abstraction (offline / Ollama / OpenAI-compatible), hardware-aware profile
  manager for 8 GB machines, idle unload, honest status; deterministic intent router so core capabilities
  work with no model at all.
- **Memory** — governed SQLite + FTS5 store with provenance, confidence, expiry, inspect/export/delete and
  secret refusal.
- **Security** — bearer auth, hashed tokens, single-use device pairing with revocation, rate limiting,
  redacted audit trail, prompt-injection fencing/taint, path containment, scrubbed child environments.
- **Integrations** — GitHub, Telegram, Google Workspace, Home Assistant, weather, Firebase push, Twilio
  adapters with honest health states; missing credentials never break the core.
- **Voice/vision control layers** — microphone session with server-side mute and barge-in; camera sessions
  with permission gate and stop semantics. Speech engines are not bundled.
- **JARVIS dashboard** — vanilla HTML/CSS/JS served locally with strict CSP: dashboard, chat with approval
  cards, task traces/evidence, memory, voice, vision, agents, tools, devices, integrations, security audit
  and settings; reduced-motion aware animated core.
- **Operations** — `matrix serve|doctor|backup|pair-code`, SQLite WAL + backup API, acceptance suite
  (A01–A25), secret scanner, CI on Windows + Ubuntu, dependency-audit job, docs.

### Security

- No `.env`, database, model weight, keystore or token is committed; secret scan runs in CI.
- Destructive/high-risk actions require exact, expiring, single-use approval.
- No generic shell tool exists; command IDs map to fixed `argv`.
- Known residual risks documented in `docs/SECURITY.md`.

### Known limitations

- No local model configured by default; free-form chat degrades with an honest message.
- Speech-to-text/text-to-speech engines are not bundled (voice control layer only).
- OCR requires Tesseract; A15 is skipped when absent.
- Android companion is a scaffold and was not built in this environment.
- Windows UI automation (pywinauto) and Playwright are not implemented yet.
