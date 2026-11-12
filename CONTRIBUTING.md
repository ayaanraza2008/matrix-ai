# Contributing to MATRIX

MATRIX is a personal assistant with real authority over a PC. That raises the bar for every change.

## Ground rules

1. **Read before writing.** Inspect the relevant subsystem and its tests first.
2. **Never add a generic execution tool.** No `run_shell(command)`, `exec(code)`, `eval`, or
   model-supplied argv. Commands are IDs mapped to fixed `argv` in configuration.
3. **Every tool declares** `permission_scope`, `risk`, `timeout_seconds`, `idempotent`, an `extra="forbid"`
   Pydantic input model and a `verify()` that returns evidence. A tool that cannot verify its outcome must
   not claim success.
4. **Policy is server-side.** UI checks are not enforcement. `PolicyEngine.evaluate_call` must run before
   any side effect.
5. **No fake success.** If a provider is unconfigured, return `503`/`missing_credentials`; never fabricate a
   sent message, a transcript, an OCR result or a deployment.
6. **Secrets never enter Git, logs or memory.** Use environment variables; run the scanner before pushing.
7. **Small commits.** One logical change, descriptive message, tests included.

## Development

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

python -m pytest            # hermetic: no network, no credentials
python -m ruff check .
python -m matrix_ai.security.secret_scan .
```

Optional extras: `[voice]` (faster-whisper), `[vision]` (OpenCV, pytesseract, Pillow), `[browser]` (Playwright).

## Adding a tool

1. Create the input model and tool class in `src/matrix_ai/tools/`.
2. Pick the narrowest `permission_scope` (`domain.action`) and the honest risk class.
3. Implement `run_sync` with bounded output; raise `MatrixError` subclasses for expected failures.
4. Implement `verify()` with concrete checks (hash read-back, path absence, metric presence, exit code).
5. Register it in `tools/registry.py`; a duplicate name raises.
6. Add tests for: happy path, schema rejection, permission denial, verification, and any traversal/SSRF case.
7. Update `docs/CAPABILITY_MATRIX.md` and, if relevant, `docs/ARCHITECTURE.md`.

## Adding an integration

Adapters live in `integrations/adapters.py`. They must be optional, time-bounded, never run at startup, and
report `missing_credentials` / `permission_denied` / `rate_limited` / `unavailable` truthfully. Never log or
return the secret itself.

## Tests

- Acceptance tests are named after blueprint IDs (`test_a05_...`) and marked `acceptance`.
- Tests must be hermetic: temporary SQLite, generated tokens, no outbound network.
- Blocked tests are skipped with a reason, never reported as passing.

## Pull requests

Include: what changed, why, exact files, test output, security impact, and remaining limitations. CI must be
green on Windows and Ubuntu. Do not rewrite history, change visibility or delete branches without explicit
owner authorisation.
