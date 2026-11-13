# Acceptance Report — v0.1.0

**Repository:** https://github.com/ayaanraza2008/matrix-ai
**Branch:** `main`
**Date:** 2026-11-12
**Environment:** Windows 11 `10.0.26300`, Python 3.12.14, 7 562 MB RAM, no CUDA, no Android SDK, no Tesseract, no Ollama

## 1. Automated test summary

```text
$ python -m pytest
137 passed, 1 skipped in 12.61s        # before the dashboard suite
141 collected, 140 passed, 1 skipped   # after adding test_web_ui.py
```

| Suite | Tests | Purpose |
|---|---|---|
| `test_a01_startup_health.py` | 5 | startup, public health, auth, no secret leakage |
| `test_a02_model_offline.py` | 2 | honest offline chat + model status |
| `test_a03_tool_schema.py` | 4 | unknown tool, extra/wrong args rejected |
| `test_a04_permission_enforcement.py` | 4 | server-side scope + autonomy denial |
| `test_a05_destructive_approval.py` | 5 | exact approval, deny, expiry, binding hash |
| `test_a06_cancellation.py` | 3 | cancel running/pre-start, emergency stop |
| `test_a07_verification.py` | 3 | no COMPLETE without evidence |
| `test_a08_retry.py` | 4 | idempotent retry/backoff, non-idempotent never retries |
| `test_a09_memory.py` | 5 | memory roundtrip, provenance, secret refusal |
| `test_a10_memory_deletion.py` | 3 | retrieval/FTS removal after delete |
| `test_a11_voice.py` | 6 | mute refusal, barge-in, honest 503s |
| `test_a13_vision.py` | 4 | permission gate, stop semantics |
| `test_a15_ocr.py` | 1 | skipped — Tesseract not installed |
| `test_a16_command_allowlist.py` | 6 | command IDs only, no arbitrary shell |
| `test_a17_path_traversal.py` | 4 | traversal/absolute escape refused |
| `test_a18_prompt_injection.py` | 3 | injection markers, taint forces approval |
| `test_a19_device_pairing.py` | 3 | pairing, single-use code, revocation |
| `test_a20_reconnect_idempotency.py` | 3 | no duplicate execution on replay |
| `test_a21_integrations.py` | 3 | missing credentials do not crash core |
| `test_a22_secret_scan.py` | 4 | clean repo + scanner self-test |
| `test_a23_backup_restore.py` | 3 | backup integrity + restore |
| `test_a24_resource_budget.py` | 4 | metrics, WAL, RSS ceiling |
| `test_a25_end_to_end.py` | 1 | multi-step task with trace + evidence |
| `test_planner.py` | 14 | plan parsing, intent routing |
| `test_policy_engine.py` | 14 | risk × autonomy × scope × taint |
| `test_security_primitives.py` | 9 | redaction, injection detection, memory guards |
| `test_state_machine.py` | 7 | transition graph invariants |
| `test_web_and_providers.py` | 11 | SSRF guards, cloud-model gate |
| `test_web_ui.py` | 4 | dashboard assets + CSP |

Lint: `ruff check .` → **All checks passed**.
Secret scan: `python -m matrix_ai.security.secret_scan .` → **clean**.

## 2. Acceptance suite (blueprint §16)

| ID | Test | Status | Evidence / scope |
|---|---|---|---|
| A01 | Fresh startup | **PASS** | `test_a01_startup_health.py` — health is public, DB `ok`, model `unconfigured`, token absent from responses |
| A02 | Local chat + offline handling | **PASS** (offline path) | `test_a02_model_offline.py` — no model configured; chat returns PARTIAL with honest message + `answer.degraded` audit. **Real local model not configured on this PC.** |
| A03 | Tool schema validation | **PASS** | unknown tool 404, extra field 422, wrong type 422, parser rejects forged args |
| A04 | Permission enforcement | **PASS** | missing scope and L0 autonomy both blocked server-side; no file created |
| A05 | Destructive action approval | **PASS** | WAITING_FOR_USER before execution, approve → verified delete, deny → file intact, expired approval refused, hash binds exact args |
| A06 | Cancellation | **PASS** | running task → CANCELLED, no new tool calls; pre-start cancel terminal; emergency stop blocks new work |
| A07 | Verification | **PASS** | broken verifier → FAILED/PARTIAL, aggregate evidence `verified: false`; healthy tool → confidence 0.95 |
| A08 | Safe retry | **PASS** | idempotent tool 3 attempts then success (retry_count persisted); non-idempotent 1 attempt |
| A09 | Memory retrieval | **PASS** | search returns item with `source`, `confidence`, timestamps; tool path works |
| A10 | Memory deletion | **PASS** | item gone from search, list and FTS index |
| A11 | Voice mute | **PASS** (control) | muted session refuses capture with `409 microphone_muted`; status reflects mute |
| A12 | Voice interruption | **PASS** (control layer) | barge-in cancels the speaking task via a test TTS double; **no real TTS engine installed**, `/api/voice/speak` returns 503 |
| A13 | Camera permission | **PASS** (session layer) | `permission_granted:false` → `403 camera_permission_denied`; no OS camera pipeline yet |
| A14 | Camera stop | **PASS** (session layer) | capture after stop → 409; active sessions 0 |
| A15 | OCR | **SKIPPED** | Tesseract binary absent on build machine; tool returns `503` honestly. Not reported as passed. |
| A16 | Desktop allowlist | **PASS** | unknown command denied; free-form command rejected by schema; metacharacters rejected; `python_version` runs and verifies |
| A17 | Path traversal | **PASS** | `../` and absolute outside paths refused; inside-workspace read/write verified |
| A18 | Browser prompt injection | **PASS** (simulated source) | injection markers detected, content fenced, tainted write escalated to HIGH approval, nothing written; **Playwright not installed**, test uses a fetch double |
| A19 | Device pairing | **PASS** | code single-use, device token works, revocation invalidates immediately, invalid code 401 |
| A20 | Remote reconnect | **PASS** (server side) | duplicate enqueue/approval execute once; cancelled task not resurrected. **Physical Android reconnect test pending** |
| A21 | Integration unavailable | **PASS** | 6/7 adapters `missing_credentials`, core task still completes |
| A22 | Secret scanning | **PASS** | repository scan clean; scanner detects a planted secret; gitignore covers runtime/secrets |
| A23 | Database backup | **PASS** | backup opens, `PRAGMA integrity_check` = ok, row counts match |
| A24 | Resource budget | **PASS** | RSS 43.3 MB at runtime build; WAL enabled; model budget 2 500 MB; one-model rule reported |
| A25 | End-to-end task | **PASS** | two-step plan completes with 2 verified tool calls, evidence confidence 0.9, full audit chain, memory persisted |

**Tally:** 24 PASS (with scope notes on A02, A11, A12, A13/A14, A18, A20), 1 SKIPPED (A15), 0 failures.

## 3. Measured performance (build machine)

| Metric | Value | Method |
|---|---|---|
| Runtime build (DB + bootstrap + wiring) | **32.4 ms** | `time.perf_counter` around `MatrixRuntime.build` |
| `GET /api/health` | **0.5 ms** | async health call, no network probe |
| Process RSS after startup | **43.3 MB** | `psutil.Process().memory_info().rss` |
| SQLite file after init | 4 KB | empty schema + WAL |
| Tools registered | 14 | `/api/tools` |
| Tool scopes | 10 | declared `permission_scope` values |
| Test suite runtime | 12.6 s | 137 tests (pre-UI) |
| RAM available during build | 1 608 MB of 7 562 MB | psutil |

The model manager reported **no fitting profile** with only 1.6 GB free — the correct answer on an 8 GB PC
that is already busy. Nothing was loaded or swapped in.

## 4. Security review

- No secrets in the repository (scan clean); owner token generated locally and stored hashed.
- All private routes require a bearer token; rate limiting enabled.
- Approval binding, path containment, command allowlisting, SSRF guard, prompt-injection taint and
  emergency stop are covered by tests (see `docs/SECURITY.md`).
- Residual risks documented: DNS-rebinding TOCTOU in `web.fetch_public`, localStorage token storage,
  no encrypted-at-rest DB, no Android build verification.

## 5. Known limitations / blockers

| Item | Status | Owner action |
|---|---|---|
| Local model | Not configured | Install Ollama, `ollama pull qwen2.5:0.5b-instruct-q4_K_M`, set `MATRIX_MODEL_PROVIDER=ollama` |
| Speech engines | Optional adapters implemented in stacked PR #14; not yet merged | Install `matrix-ai[voice]`, configure a local Piper voice, then complete real Windows microphone/speaker E2E validation |
| OCR | Blocked | Install Tesseract + `pip install "matrix-ai[vision]"` |
| Android APK | Scaffold | Build with Android Studio (see `docs/ANDROID.md`) |
| Optional integrations | Unconfigured | Provide least-privilege credentials |
| UI automation / browser | Not implemented | Phase 5 |
| Installer / signed release | Not implemented | Phase 9 |

## 6. Reproduce this report

```powershell
git clone https://github.com/ayaanraza2008/matrix-ai.git
cd matrix-ai
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m matrix_ai.security.secret_scan .
matrix doctor
```

CI runs the same suite on Windows and Ubuntu Python 3.12 (`.github/workflows/ci.yml`).

## 7. Independent verification log

| Check | Result |
|---|---|
| GitHub Actions, run `43189181318` (commit `675c3fc`) | **success** — `test (windows-latest, py3.12)`, `test (ubuntu-latest, py3.12)`, advisory dependency audit |
| Clean clone of `main` into a fresh directory | 116 tracked files; `matrix_ai` imports; `141 tests collected`, **140 passed / 1 skipped**; secret scan clean |
| Live server smoke test (`uvicorn`, `127.0.0.1:8790`, temporary home) | `GET /api/health` → `ok`; dashboard `200` with strict CSP; assets `200` |
| Live task over HTTP (no test harness) | “system status” → `COMPLETE`, evidence `verified=true`; write file → `COMPLETE`; delete → `WAITING_FOR_USER` (risk `high`) → approve → `COMPLETE` with “confirmed it is gone” |
| Secret handling | owner token read from the generated file; never printed by health/status/settings |
| Repository | public, default branch `main`, description + topics set, MIT license, README rendered |

The live HTTP run exercised the same code path as the acceptance tests but through real sockets, the real
dashboard assets and the real CLI bootstrap — no mocked transport.



## 8. Stacked implementation and CI addendum (not yet merged into main)

This report's measured baseline above describes the `main` branch. The following
incremental work exists on the dependent draft-PR chain and must not be treated as
released until the full chain is reviewed and merged in order:

| PR | Increment | Current verification | Remaining proof |
|---|---|---|---|
| [#14](https://github.com/ayaanraza2008/matrix-ai/pull/14) | Optional faster-whisper STT + Piper TTS adapters | Windows + Ubuntu Python 3.12 CI passed | Real target-PC microphone/speaker test, model download/cache and cancellation |
| [#16](https://github.com/ayaanraza2008/matrix-ai/pull/16) | Owner-bounded recurring jobs, Windows per-user logon task, private Tailscale Serve | Windows + Ubuntu Python 3.12 CI passed | Real Task Scheduler and tailnet ACL/Serve validation |
| [#18](https://github.com/ayaanraza2008/matrix-ai/pull/18) | Persistent core-health state and optional Telegram health alerts | Windows + Ubuntu Python 3.12 CI passed | Live Telegram delivery/recovery and host-offline behaviour |
| [#19](https://github.com/ayaanraza2008/matrix-ai/pull/19) | Private two-way Telegram chat bridge | Windows + Ubuntu Python 3.12 CI passed | Live bot delivery, reconnect, task-result and stop validation |
| [#21](https://github.com/ayaanraza2008/matrix-ai/pull/21) | Telegram throttling/approval follow-up, configurable Tailscale port, Android release transport hardening, Python 3.11 matrix, Android debug/release build and CycloneDX SBOM | Latest verified run before artifact-upload/SBOM commit: Windows + Ubuntu Python 3.11/3.12 and Android debug/release assemble passed; current CI re-run is active | Latest CI rerun, APK/SBOM artifact publication, physical Android device test, signed release package |

All four PRs are drafts and stacked in dependency order (#14 → #16 → #18 → #19).
Their CI success proves the committed test suite on GitHub-hosted Windows/Ubuntu
runners; it does not prove physical-device, real audio, configured external-service,
Android, installer or production-uptime behaviour. See the PRs for the exact
head commits and latest workflow results.


## 9. Integrated release candidate — exact-head evidence (2026-11-13)

The integrated release candidate was squash-merged through [PR #72](https://github.com/ayaanraza2008/matrix-ai/pull/72) as `f472e0e9f164c0f15bfc2a72b3288f073109f704`. Exact-head GitHub Actions run [#43424775584](https://github.com/ayaanraza2008/matrix-ai/actions/runs/43424775584) completed with all seven jobs successful:

- Windows Python 3.11 and 3.12 full CI.
- Ubuntu Python 3.11 and 3.12 full CI.
- Android debug and unsigned release APK assembly.
- Astryx frontend build.
- Blocking dependency audit.
- CycloneDX SBOM generation and artifact upload.

The merged candidate includes the consolidated Telegram/autonomy/integration/notification stack and dependency refresh. This supersedes historical statements in earlier sections that the integration candidates #68/#69 were still drafts and unmerged.

### Production acceptance remains outstanding

CI does not establish live acceptance on the owner's environment. Still required: Windows install/startup/restart/upgrade/rollback; Tailscale ACL and remote-device verification; real Telegram owner pairing, send/receive, restart/replay and emergency-stop tests; actual microphone/speaker and local model validation; physical Android pairing/revocation/network testing and owner-signed release APK; least-privilege OAuth and harmless live smoke tests for configured providers; backup/restore and incident/rollback rehearsal; independent security review. See [RELEASE_STATUS_2026-11-13.md](RELEASE_STATUS_2026-11-13.md) for the current gate checklist.

Known residual risks must remain visible: public-fetch DNS-rebinding/TOCTOU, at-least-once Telegram replies/provider side effects, unsigned Android release output, and the need for owner-controlled credentials and devices. Production status remains **NOT CERTIFIED**.
