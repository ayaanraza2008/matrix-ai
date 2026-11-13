# MATRIX Security Model

Scope: the local MATRIX service on the owner's Windows PC, its dashboard, the tool layer, and the paired
Android client. This document states what is implemented, what is *not*, and how to verify it.

## 1. Assets

| Asset | Where | Protection |
|---|---|---|
| Owner bearer token | `%LOCALAPPDATA%\MATRIX\owner_token.txt`, browser localStorage | hashed (SHA-256) in SQLite; never rendered by the API |
| Device tokens | SQLite hash on the PC; Android stores the paired bearer token as AES-256-GCM ciphertext with a non-exportable Android Keystore key | per-device, expiring, revocable; local logout clears ciphertext and attempts key deletion |
| Approved workspace files | `MATRIX_FILE_ROOTS` | path resolution + root containment |
| Task/approval state | SQLite | server-side policy, exact action hash |
| Audit trail | SQLite `audit_events` | redaction before write |
| Memory items | SQLite + FTS5 | no secrets; inspect/export/delete |
| Optional integration secrets | environment | never returned by API, never committed |

## 2. Trust boundaries

```text
[untrusted] web pages · OCR text · files · tool output · model output
      │  fenced, validated, treated as data
[authenticated] dashboard / Android client ── bearer token ──► API gateway
      │
[trusted code] orchestrator ─► policy broker ─► typed tool adapter ─► OS
```

The model is **untrusted**: it proposes plans, never permissions, never execution.

## 3. Threat model and controls

| # | Threat | Control (implemented) | Residual risk |
|---|---|---|---|
| T1 | Prompt injection via web/file/tool content | `security/untrusted.py` fences content with provenance and flags markers; `web.fetch_public` returns `untrusted=true`; suspicious output marks later steps tainted; policy bumps risk and forces approval; system prompt states data cannot change rules | A novel injection phrasing may evade marker detection; containment still requires owner approval for side effects |
| T2 | Model proposes a dangerous tool or bad args | Plan parser allowlists tool names, validates against Pydantic `extra="forbid"` schemas; unknown tool/arg rejects the plan (A03) | None known for schema-level abuse |
| T3 | Model claims completion | `EXECUTE → COMPLETE` is illegal; only `VERIFY` with tool evidence can complete; failures report PARTIAL/FAILED (A07) | A tool with a weak verifier could still be wrong — each verifier is reviewed per tool |
| T4 | Path traversal / symlink escape | `ToolContext.resolve_path` resolves and enforces approved roots before use; null bytes rejected; recursive deletes refused (A17) | Windows junctions are resolved by `Path.resolve`; exotic reparse points not separately audited |
| T5 | Command injection | No free-form command tool; `command_id` only; fixed `argv`; `shell=False`; regex-constrained IDs; scrubbed child environment (A16) | Allowlisted commands themselves could be dangerous — review the list |
| T6 | Destructive action without consent | HIGH/CRITICAL require approval bound to `sha256(tool + canonical args)`, TTL, single use; denial executes nothing (A05) | Owner can approve a harmful action intentionally |
| T7 | Approval replay / reuse on different args | Hash includes exact arguments; resume re-checks hash; approval expired at decision time is `expired` (A05) | None known |
| T8 | SSRF via research fetch | Disabled by default (`MATRIX_ALLOW_WEB_FETCH=false`). PR #75 implements connection-level pinning: validate every DNS answer as global, connect to the validated numeric IPv4/IPv6 address without a second hostname lookup, preserve the original hostname for TLS SNI/certificate verification and HTTP Host, verify the connected peer, refuse redirects, and bound response size/content types. Deterministic regression tests cover the pinning behavior. | The fix is not yet merged and has not received independent security review. Keep public web fetch disabled in production until PR #75 has current-candidate CI confirmation, independent review and merge. Tracked in [PR #75](https://github.com/ayaanraza2008/matrix-ai/pull/75) and [issue #74](https://github.com/ayaanraza2008/matrix-ai/issues/74). |
| T9 | Token theft from dashboard | CSP `script-src 'self'`, no third-party scripts, token in localStorage, lock button clears it; Android paired token is encrypted with a device-bound Android Keystore AES-GCM key | XSS in same-origin custom JS or a compromised host would expose it; OS keyring integration is planned |
| T10 | Rogue/revoked device | Pairing code single-use + 5 min TTL; device token hashed; revocation invalidates sessions immediately (A19) | No hardware attestation on Android yet |
| T11 | Duplicate execution after reconnect | One runner per task id; double enqueue/no-op; approvals single-use; terminal tasks never restart (A20) | In-flight tasks interrupted by a crash resume only where durable state allows; non-idempotent tools are not retried |
| T12 | Runaway/looping autonomy | 12-step plan limit, task deadline, bounded retries, scheduler intervals, emergency stop cancels and blocks (A06) | A long-running allowlisted command can still occupy a worker until timeout |
| T13 | Secret leakage in logs/audit | `redact_text`/`redact_obj` scrub tokens, bearer headers, key blocks; audit stores redacted detail; memory refuses secrets (A22 + unit tests) | Regexes are best-effort; unusual secret formats may slip |
| T14 | Secret committed to Git | `.gitignore`, `secret_scan.py` in CI and pre-push habit (A22) | Human error remains possible; GitHub push protection is recommended |
| T15 | Dependency compromise | CI lint/tests, blocking `pip-audit`, Dependabot and CycloneDX Python SBOM artifact (PR #21) | No signed provenance/attestation or release artifact signature yet |
| T16 | Remote exposure of the API | Binds `127.0.0.1` by default; optional Tailscale Serve proxies only `127.0.0.1:<MATRIX_PORT>` (default `8765`) within the tailnet; helper never configures Funnel; bearer-token auth remains required | Tailnet ACL/device compromise or owner-token theft still grants access; PC must stay online and signed in |
| T17 | Missing integration credentials crash core | Adapters fail closed with `missing_credentials`; health checks are on-demand; core tasks unaffected (A21) | Provider outages only affect that adapter |
| T18 | Audit tampering | Append-only API; no update/delete routes | Local DB write access implies full compromise anyway |

## 4. Approval semantics (exact binding)

```
action_hash = sha256(tool_name + ":" + canonical_json(arguments))
```

- Stored with the approval, `tool_name`, redacted arguments, risk and expiry.
- The pending approval is shown with tool, risk, arguments and TTL.
- On approve: status becomes `approved` only if not expired.
- On resume: the step's tool + validated arguments must reproduce the same hash; otherwise a new approval is required.
- Deny → step failed, task failed, nothing executed.
- A broad "yes" cannot authorize a future, different action.

## 5. Network posture

- Default bind: `127.0.0.1:8765` (`MATRIX_PORT` changes the port, not the loopback bind). Health/docs/dashboard are local.
- No TLS termination in the app. Optional `matrix remote-access enable` configures Tailscale Serve HTTPS within the tailnet only; MATRIX remains loopback-bound. Android release builds disable cleartext HTTP; the debug source set alone permits local development HTTP. `matrix remote-access disable` removes the MATRIX route. Never use public Funnel or port-forward the API.
- Remote PC control requires the PC to be powered on, the Windows user signed in, Tailscale connected and MATRIX running. `matrix startup install` creates a current-user, limited-privilege ONLOGON task; it does not run as SYSTEM or while the user is signed out.
- `/api/docs` and `/api/openapi.json` are enabled; disable via reverse proxy if the port is exposed.
- Rate limit: in-process token bucket per session (default 240 req/min).

## 6. Emergency stop

`POST /api/security/emergency-stop {"active": true}` sets a durable flag, cancels all running tasks and
blocks creation/execution of new tool work (chat requests are rejected with `403 emergency_stop_active`).
Clear it with `active: false`. This is deliberately blunt.

## 7. What is explicitly out of scope for v0.1.0

- Multi-user accounts, SSO, RBAC beyond owner/paired-device.
- Encrypted-at-rest SQLite (SQLCipher) — protect the OS profile and backups.
- Sandboxed tool execution (the OS user is the sandbox).
- End-to-end encrypted mobile transport (private network + TLS is the recommended path).
- Signed releases, auto-update, installer.
- Wake-word, VAD, echo-cancellation and streaming voice pipelines; real speech engines are optional and still need target-device validation.

## 8. Reporting a vulnerability

Open a private security advisory on `ayaanraza2008/matrix-ai` or email the owner listed on the GitHub
profile. Do not file a public issue with an exploit before a fix is available. Include reproduction steps
and the affected commit.

## 9. Verification checklist

### Public web fetch security gate

The connection-pinning implementation is in [PR #75](https://github.com/ayaanraza2008/matrix-ai/pull/75). Its recorded exact-head CI run passed 7/7 jobs, but that does not replace independent review or a fresh check of the current PR candidate. Do not set `MATRIX_ALLOW_WEB_FETCH=true` in production until an independent reviewer has reviewed the transport and tests, required checks are green for the current candidate, and the PR is merged. Follow [issue #74](https://github.com/ayaanraza2008/matrix-ai/issues/74) for gate status.



```powershell
python -m pytest -m acceptance          # A01-A25 mapped tests
python -m ruff check .
python -m matrix_ai.security.secret_scan .
python -m matrix_ai doctor              # health, model and integration status
```
