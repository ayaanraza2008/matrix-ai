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
| Speech engines | Blocked | Install faster-whisper + Piper (Phase 3) |
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

## 8. Supplemental voice milestone — PR #10

**Branch:** `feat/local-whisper-transcription`
**Scope:** optional faster-whisper local STT, browser push-to-talk recording, API event-loop isolation, metadata-only audit.

Three regression tests were added:
- `test_local_transcription_loads_cpu_model_once_and_keeps_audio_in_memory` — verifies cached lazy model loading, CPU/INT8 settings, in-memory audio and returned transcript metadata using a test double.
- `test_local_transcription_rejects_audio_over_duration_limit` — verifies the configured audio-duration guard.
- `test_transcribe_api_offloads_and_audits_metadata` — verifies route delegation and that the audit record contains provider/language/duration only, not transcript content.

**GitHub Actions:** run `43206453437`, commit `c5819a3cbef5697658302471c102b89d132b39fd` — **PASS** on Windows and Ubuntu, Python 3.12. Lint, secret scan, full pytest suite, byte-compilation and CLI smoke test passed on both operating systems; the advisory dependency audit also passed.

**Not yet verified:** the CI tests use a mocked Whisper model. Actual microphone access, first-run model download, English/Hindi recognition quality, CPU latency and peak RAM must still be measured on the owner's Windows PC. The optional dependency is not installed by default. Text-to-speech remains blocked/not implemented.

