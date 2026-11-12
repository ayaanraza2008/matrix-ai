# MATRIX Architecture

## 1. Principles

1. **One brain, many tools.** A single orchestration path owns task state; tools are narrow, typed adapters.
2. **Local-first.** SQLite, the workspace, and (optionally) a local model. Cloud models are opt-in.
3. **Policy is server-side.** The UI and the model cannot grant permissions.
4. **Evidence over claims.** `COMPLETE` requires tool-produced verification evidence.
5. **Fail safely.** Uncertainty, policy refusal, timeouts and cancellation end in a reported stop — never a retry storm.
6. **8 GB discipline.** One primary model; idle unload; bounded outputs; no heavyweight framework stack.

## 2. Runtime layers

| Layer | Code | May do | Must not do |
|---|---|---|---|
| Dashboard / CLI | `web/`, `cli.py` | Display state, collect input, show approvals | Hold API secrets (token lives in browser localStorage only), bypass policy |
| HTTP gateway | `app.py`, `api/routes.py` | Auth, validation, rate limit, routing | Execute shell or tools directly |
| Orchestrator | `core/orchestrator.py` | Drive states, retry, cancel, request approval | Grant itself permissions |
| Policy broker | `core/policy.py` | Classify risk, check scope/autonomy, bind approvals | Trust model-declared risk |
| Tool adapters | `tools/` | One documented capability with typed I/O | Accept arbitrary code, paths or commands |
| Model runtime | `models/` | Propose plans/answers | Touch the OS or grant approvals |
| Memory | `memory/service.py` | Governed retrieval/storage/deletion | Persist secrets |
| Audit | `storage/store.py` | Append redacted events | Store raw prompts, tokens or payloads |
| Storage | `storage/db.py` | SQLite WAL, migrations, backup | Hold plaintext credentials |

## 3. Task lifecycle

```text
RECEIVED
  → UNDERSTAND        (heuristic intent router or model plan, schema-validated)
  → POLICY_CHECK      (plan-level scope + risk gate)
  → PLAN              (steps persisted with dependencies)
  → TOOL_SELECTION    (per-step schema + policy + taint evaluation)
  → APPROVAL_REQUIRED → WAITING_FOR_USER   (only for HIGH/CRITICAL or tainted MEDIUM+)
  → EXECUTE           (timeout, bounded retries, cancellation checks)
  → VERIFY            (tool verifier produces evidence)
  → next step | COMPLETE | PARTIAL | FAILED | CANCELLED
```

Illegal transitions raise (`core/state_machine.py`). `EXECUTE → COMPLETE` is not in the graph, so a model
cannot shortcut verification. Resuming after an approval re-runs policy and matches the approval's action
hash; a mismatched hash cannot execute.

### Autonomy and risk

| Risk | Examples | Default | Minimum autonomy |
|---|---|---|---|
| LOW | read telemetry, list files, search memory | auto | L0 |
| MEDIUM | create a file, open an app, OCR, fetch public page | auto within scope + audit | L2 |
| HIGH | overwrite, delete, close a process | **exact approval** | L2 |
| CRITICAL | external messages, publishing, spending, security settings | **exact per-action approval** | L2 |

Untrusted content bumps risk one class (`MEDIUM → HIGH`), which forces approval. The policy engine, not the
plan, decides this.

## 4. Tool contract

Every tool declares: `name`, `description`, `permission_scope`, `risk`, `autonomy_level`, `timeout_seconds`,
`idempotent`, `returns_untrusted`, `requires_verification`, a Pydantic input model (`extra="forbid"`) and a
`verify()` implementation.

| Tool | Scope | Risk | Idempotent | Verification |
|---|---|---|---|---|
| `system.get_status` | system.read | low | yes | required metrics present |
| `files.list` | files.read | low | yes | listing returned |
| `files.read_text` | files.read | low | yes | content + SHA-256 |
| `files.stat` | files.read | low | yes | metadata present |
| `files.write_text` | files.write | medium/high (overwrite) | no | read-back hash equality |
| `files.delete` | files.write | high | no | path absent afterwards |
| `files.move` | files.write | medium/high | no | destination exists, source absent |
| `apps.open` | apps.launch | medium | no | process pid / protocol launch |
| `apps.close` | apps.control | high | no | pid gone |
| `shell.run_allowed` | shell.allowlisted | per command | no | exit code 0 |
| `memory.search` | memory.read | low | yes | list returned |
| `memory.save` | memory.write | medium | no | persisted id |
| `web.fetch_public` | web.read | medium | yes | HTTP 200 + text |
| `vision.ocr_image` | vision.read | medium | yes | OCR completed (approximate) |

There is **no** generic shell/exec/eval tool. Command IDs map to fixed `argv` lists; arguments never reach
the command line.

## 5. Model strategy

- Provider abstraction: `NoModelProvider`, `OllamaProvider`, `OpenAICompatibleProvider` (llama.cpp, LM Studio, vLLM).
- Cloud endpoints are refused unless `MATRIX_ALLOW_CLOUD_MODELS=true` **and** the host is non-local; local endpoints pass.
- Deterministic intents (status, files, memory, OCR, allowlisted commands) do not need a model at all.
- Free-form chat/planning uses `plan.v1` / `chat.v1` prompts with JSON-only planning and schema validation.
- A failed plan, invalid JSON, unknown tool or bad arguments degrade to the deterministic router — never to
  unvalidated execution.
- Hardware-aware profiles (0.5B/1.5B/3B/7B) with declared RAM footprints; one model at a time; idle unload
  via Ollama `keep_alive=0`; `/api/models` reports provider health, latency and budget.

## 6. Memory

SQLite is authoritative. FTS5 is an index only. Items carry `kind`, `source`, `confidence`, timestamps and
optional expiry. Deletion soft-deletes the row and removes the index entry, so retrieval cannot resurrect
it. Credentials are refused at the boundary. Export returns JSON; retention sweep removes expired items.

## 7. Persistence

| Table | Purpose |
|---|---|
| `users`, `devices`, `sessions`, `pairing_codes` | identity, pairing, revocation |
| `tasks`, `plan_steps`, `tool_calls` | durable lifecycle + trace |
| `approvals` | exact action hash, TTL, decision |
| `audit_events` | redacted append-only trail |
| `memory_items` (+ `memory_fts`) | governed memory |
| `integrations`, `scheduled_jobs`, `system_flags`, `idempotency` | optional adapters, automation, emergency stop |

WAL mode, `busy_timeout`, parameterised SQL, explicit transactions for plan writes, `backup()` via SQLite's
backup API and `PRAGMA integrity_check`.

## 8. Concurrency, cancellation, retries

- Each task runs as one `asyncio` task; a registry prevents duplicate runners for the same task id.
- Blocking tools run in a worker thread (`asyncio.to_thread`) under `wait_for(timeout)`.
- Cancellation sets a flag, rescinds the waiter, and cancels the runner; interrupted steps are recorded as
  cancelled/failed; no new steps start.
- Retries apply only to **idempotent** tools at `≤ MEDIUM` risk with exponential backoff + jitter (max 2 extra attempts).
- Approval waiters are futures; if the runner exits on timeout the task stays `WAITING_FOR_USER` and an owner
  decision resumes it from durable state.

## 9. Dashboard

Vanilla HTML/CSS/JS served by the same process (`/` + `/assets`). Strict CSP (`script-src 'self'`), no inline
script, no build step. The animated core is decorative and pauses when the tab is hidden or
`prefers-reduced-motion` is set. Every panel renders server truth: states, evidence, approval cards and
errors — no fabricated success.

## 10. Deliberate boundaries

- The orchestrator never edits its own policy or grants scopes.
- The API never executes a tool without `PolicyEngine.evaluate_call`.
- The dashboard never claims an action succeeded before a terminal task state + evidence.
- Android is a client, not a second brain; it pairs with a short-lived code and can be revoked immediately.
