# MATRIX

**A local-first, JARVIS-inspired personal AI assistant for Windows PC + Android.**
One orchestrator, typed tools, a real policy engine, and an honest dashboard — built free-first, with no paid API in the core path.

[![CI](https://github.com/ayaanraza2008/matrix-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/ayaanraza2008/matrix-ai/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

> MATRIX is not movie-level intelligence and does not claim to be. It is a dependable assistant core: it can read system state, work inside an approved workspace, search governed memory, OCR images (with Tesseract installed), fetch public pages defensively, run allowlisted commands, and *prove* what it did. Anything sensitive stops for exact approval.

---

## What is real right now (v0.1.0)

| Area | Status | Evidence |
|---|---|---|
| Startup crash recovery | **Implemented** | `core/orchestrator.py`, `test_startup_recovery.py`, `docs/RECOVERY.md` |
| Central orchestrator + explicit state machine | **Implemented** | `src/matrix_ai/core/orchestrator.py`, `state_machine.py`, tests `test_orchestrator_lifecycle.py` |
| Server-side policy engine, autonomy L0–L4, risk classes | **Implemented** | `core/policy.py`, `test_policy.py` |
| Exact approval binding (hash of tool + args + TTL) | **Implemented** | `test_a05_destructive_approval.py` |
| Typed tools with schema/allowlist/timeouts/verification | **Implemented** | `tools/`, `test_a03_tool_schema.py` |
| Scoped filesystem + path-traversal defence | **Implemented** | `tools/file_tools.py`, `test_a17_path_traversal.py` |
| Allowlisted applications and commands only | **Implemented** | `tools/app_tools.py`, `tools/shell_tools.py`, `test_a16_command_allowlist.py` |
| Governed SQLite memory + FTS5, delete removes from retrieval | **Implemented** | `memory/service.py`, `test_a09_memory.py`, `test_a10_memory_deletion.py` |
| Model provider abstraction (Ollama / OpenAI-compatible / offline) | **Implemented** | `models/base.py`, `test_a02_model_offline.py` |
| Hardware-aware model manager (8 GB RAM discipline) | **Implemented** | `models/manager.py`, `test_a24_resource_budget.py` |
| Local model benchmark (English/Hindi, schema, tool choice, latency, RSS) | **Implemented** | `models/benchmark.py`, `test_model_benchmark.py` |
| 283 independent specialist worker instances (bounded parallel analysis and task reports) | **Implemented** | `core/worker_pool.py`, `test_agent_workers.py` |
| JARVIS dashboard (chat, tasks, memory, devices, security) | **Implemented** | `src/matrix_ai/web/` |
| Voice session control: mute, barge-in, push-to-talk state | **Implemented (control layer)** | `voice/manager.py`, `test_a11_voice_mute.py` |
| Speech-to-text / text-to-speech engines | **Blocked** | No engine installed; API returns `503` honestly |
| Vision capture sessions + stop semantics | **Implemented** | `vision/manager.py`, `test_a13_vision_permission.py` |
| OCR | **Implemented, environment-dependent** | Needs Tesseract + `pip install 'matrix-ai[vision]'` |
| Device pairing, revocation, rate limits, audit | **Implemented** | `security/auth.py`, `test_a19_device_pairing.py` |
| Optional integrations with health checks | **Adapters implemented** | Google/Twilio/Tailscale/FCM not configured |
| Android companion | **Scaffolded** | `android/` — needs Android SDK; not built in CI |
| Signed release / installer | **Planned** | Phase 9 |

Full evidence and the exact pass/skip/blocked counts: [`docs/ACCEPTANCE_REPORT.md`](docs/ACCEPTANCE_REPORT.md).

MATRIX includes the full 283-profile Agency Agents specialist library as a pinned Git submodule. Each profile has its own addressable worker instance, lock, status and run counters. Selected workers independently analyze a task and return bounded reports to the coordinator; all real tool actions still pass through MATRIX's central policy, exact-approval and verification pipeline. They share the configured inference provider rather than duplicating 283 model weights. See [`docs/AGENCY_AGENTS.md`](docs/AGENCY_AGENTS.md) for installation, API and safety details.

MATRIX also has a pinned, lazy-loaded catalogue of 18 optional capability repositories (OpenWhispr, YouTube Automation Agent, Browser Use, Mobile MCP, REA, Craft Anything, HexStrike, ECC, OpenViking, AgentMemory, scientific skills and more). Their features are selected by task intent; only owner-installed references and explicitly connected services are used. Source install never runs upstream scripts. See [`docs/EXTERNAL_ADDONS.md`](docs/EXTERNAL_ADDONS.md) for pinned licences, install/connect instructions, API routes and safety boundaries.

---

## Quick start (Windows PowerShell)

```powershell
# 1. Get the code
git clone --recurse-submodules https://github.com/ayaanraza2008/matrix-ai.git
cd matrix-ai

# 2. Create an isolated venv
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install core + dev tools
pip install -e ".[dev]"

# 4. Run the tests (no network, no secrets required)
python -m pytest

# 5. Start MATRIX
matrix serve
```

Open **http://127.0.0.1:8765**, then paste the owner token printed by the CLI (also stored at `%LOCALAPPDATA%\MATRIX\owner_token.txt`). The dashboard keeps it in `localStorage` on your machine only.

Optional local brain (recommended, free):

```powershell
# Install Ollama for Windows, then:
ollama pull qwen2.5:0.5b-instruct-q4_K_M
$env:MATRIX_MODEL_PROVIDER = "ollama"
$env:MATRIX_MODEL_NAME = "qwen2.5:0.5b-instruct-q4_K_M"
matrix serve
```

MATRIX works without a model: deterministic tools, memory, approvals and verification keep working. Free-form chat degrades to an honest message instead of hallucinating. To measure a configured local model on English/Hindi plans without executing them, run matrix benchmark-model; see docs/SETUP.md.

---

## Configuration

Everything is environment-driven; `.env.example` documents every knob. Secrets are **never** read from source files.

| Variable | Default | Purpose |
|---|---|---|
| `MATRIX_HOME` | `%LOCALAPPDATA%\MATRIX` | Database, workspace, generated owner token |
| `MATRIX_HOST` / `MATRIX_PORT` | `127.0.0.1` / `8765` | Local API. Keep loopback unless using private networking. |
| `MATRIX_OWNER_TOKEN` | generated | Owner bearer token (stored hashed in SQLite) |
| `MATRIX_FILE_ROOTS` | `<home>\workspace` | `;`-separated roots that file tools may touch |
| `MATRIX_MODEL_PROVIDER` | `none` | `none`, `ollama`, or `openai`-compatible local server |
| `MATRIX_MODEL_RAM_BUDGET_MB` | `2500` | Model memory ceiling (8 GB PC discipline) |
| `MATRIX_MODEL_MAX_CONCURRENT_REQUESTS` | `1` | Active inference requests for the shared model |
| `MATRIX_AGENT_MAX_CONCURRENT_WORKERS` | `4` | Maximum concurrently active specialist actors |
| `MATRIX_AGENT_WORKER_TIMEOUT_SECONDS` | `90` | Per-worker execution deadline |
| `MATRIX_YOUTUBE_AUTOMATION_ENABLED` | `false` | Explicitly expose the local YouTube Automation Agent service tools |
| `MATRIX_YOUTUBE_AUTOMATION_URL` | `http://127.0.0.1:3456` | Loopback/private service endpoint; public hosts are refused |
| `MATRIX_ADDON_MCP_SERVERS` | unset | Owner-only JSON config of local MCP processes / verified OpenWhispr endpoint |
| `MATRIX_ADDON_AUTO_INSTALL_SOURCES` | `true` | Task-selected safe repositories up to 200 MB are sparse-fetched at pinned commits (source only) |
| `MATRIX_ADDON_AUTO_INSTALL_SOURCES` | `true` | Task-selected safe repositories up to 200 MB are sparse-fetched at pinned commits (source only) |
| `MATRIX_HEXSTRIKE_ALLOWED_TARGETS` | empty | Exact owner-approved hosts/IPs; HexStrike target tools are blocked until set |
| `MATRIX_ALLOW_CLOUD_MODELS` | `false` | Must be explicitly enabled for any non-local endpoint |
| `MATRIX_AUTONOMY_LEVEL` | `2` | L0–L4 default autonomy |
| `MATRIX_ALLOW_WEB_FETCH` | `true` | Public-page research tool (SSRF-guarded) |
| `MATRIX_GITHUB_TOKEN`, `MATRIX_TELEGRAM_*`, `MATRIX_HOME_ASSISTANT_*`, `MATRIX_TWILIO_*` | unset | Optional integrations |

---

## How it is built

```text
Windows PC                                     Android (scaffold)
┌─────────────────────────────────────┐        ┌──────────────────────┐
│  Clients: JARVIS dashboard (web)    │        │ Kotlin + Compose     │
│           CLI (matrix doctor)       │        │ pairing / chat /     │
└───────────────┬─────────────────────┘        │ camera / status      │
                │ bearer token                  └──────────┬───────────┘
┌───────────────▼─────────────────────┐                   │ HTTPS + token
│ FastAPI  /api/*  (auth, rate limit) │◄──────────────────┘
├─────────────────────────────────────┤
│ Orchestrator + task state machine   │  RECEIVED → UNDERSTAND → POLICY_CHECK
│ Policy engine + approval broker     │  → PLAN → [APPROVAL] → EXECUTE → VERIFY
│ Tool registry (typed, allowlisted)  │
│ Model manager (one model at a time) │
│ Memory service (SQLite + FTS5)      │
│ Audit log (redacted)                │
└───────────────┬─────────────────────┘
                │
    SQLite (WAL) + approved workspace + optional local model runtime
```

Read the design rationale in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), the threat model in [`docs/SECURITY.md`](docs/SECURITY.md), the specialist integration in [`docs/AGENCY_AGENTS.md`](docs/AGENCY_AGENTS.md), the external add-ons in [`docs/EXTERNAL_ADDONS.md`](docs/EXTERNAL_ADDONS.md), and the phased plan in [`docs/ROADMAP.md`](docs/ROADMAP.md).

### Why the state machine matters

A model can propose a plan; it cannot mark work complete. `COMPLETE` is only reachable through `VERIFY` with evidence produced by the tool itself (hash read-back for writes, path-absence for deletes, metric presence for telemetry). If verification fails, MATRIX reports **partial** or **failed** — never "done".

### Safety rules baked into the code

1. No generic `run_shell(command_from_llm)` exists. Commands are IDs from configuration with fixed `argv`.
2. File tools resolve every path and refuse anything outside approved roots (including `..` and symlink escapes).
3. HIGH/CRITICAL risk requires an approval bound to the exact action hash, with a TTL; denying executes nothing.
4. Content from web pages, files or tool output is fenced as untrusted, and suspicious output taints later actions → approval required.
5. Emergency stop cancels running tasks and blocks new execution.
6. Secrets are redacted from audit records, refused in memory, and stripped from child-process environments.

---

## Repository layout

```text
src/matrix_ai/
  api/            FastAPI routes (thin)
  core/           orchestrator, state machine, policy, agents, models
  tools/          typed adapters: system, files, apps, shell, memory, web, vision
  models/         provider abstraction, model manager, planner
  memory/         governed memory service
  storage/        SQLite schema + repositories
  security/       auth, pairing, rate limiting, redaction, prompt-injection defence
  integrations/   optional adapters with honest health states
  voice/ vision/  permission-gated session managers
  web/            JARVIS dashboard (vanilla HTML/CSS/JS, no build step)
android/          Kotlin + Compose companion scaffold
docs/             architecture, security, baseline, capability matrix, roadmap, acceptance
tests/            pytest suite mapped to acceptance IDs A01–A25
```

---

## Testing

```powershell
python -m pytest -q                       # full suite
python -m pytest -m acceptance            # acceptance IDs only
python -m pytest tests/test_a17_path_traversal.py -v
python scripts/scan_secrets.py            # secret scan before pushing
```

Tests are hermetic: no network, temporary SQLite, generated tokens, no audio or camera hardware required.

---

## Known limitations (v0.1.0)

- **No speech engines yet.** Voice session state, mute and barge-in are real and tested; STT/TTS adapters return `503` until you install faster-whisper / Piper.
- **OCR needs Tesseract** on `PATH` plus the `vision` extra.
- **No UI screenshot automation yet** — `pywinauto`/`Playwright` adapters are Phase 5.
- **Android app is a scaffold** and has not been built here (no Android SDK on the build machine).
- **Remote access is not exposed.** Use Tailscale/WireGuard; do not port-forward the API.
- **`web.fetch_public` re-resolves DNS** rather than pinning the vetted IP for the TCP connection; redirects are disabled and only public addresses are allowed, but the TOCTOU gap is documented in `docs/SECURITY.md`.
- Auto-retry covers idempotent, low/medium-risk tools with exponential backoff and jitter; non-idempotent actions never retry blindly.

---

## License

MIT — see [`LICENSE`](LICENSE). Third-party model weights (Qwen, Whisper, Piper voices) carry their own licenses; check before redistribution.


### Free local voice

Install the optional offline speech stack with `pip install -e ".[voice]"`. MATRIX uses
faster-whisper on CPU/INT8 for speech-to-text and Piper for local text-to-speech. Download a
Piper ONNX voice separately and set `MATRIX_PIPER_MODEL`; see
[`docs/VOICE_AND_TELEPHONY.md`](docs/VOICE_AND_TELEPHONY.md). The first Whisper model load may
download weights into the local cache. Speech clips are temporary and are not stored in MATRIX's
database. Phone/PSTN calls remain an optional transport and carrier charges are not free.
