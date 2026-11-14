# MATRIX owner-environment production acceptance runbook

Use this only on the Windows PC and Android handset that will actually run MATRIX. Hosted CI cannot perform these checks. Record pass/fail, timestamp, OS/app versions and sanitized evidence; never attach tokens, bot secrets, OAuth refresh tokens, private messages or personal data.

## 1. Fresh Windows install and baseline

1. Install Python 3.12 and Git.
2. Clone the repository with submodules and create an isolated environment:

```powershell
git clone --recurse-submodules https://github.com/ayaanraza2008/matrix-ai.git
cd matrix-ai
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
python -m matrix_ai doctor
python -m pytest -q
python -m ruff check . --exclude agency-agents
python -m matrix_ai.security.secret_scan .
```

3. Start MATRIX with `matrix serve`; verify the dashboard and `GET /api/health`.
4. Confirm the owner token exists in the MATRIX home directory and is never printed in logs/API responses.

**Pass:** clean install, all tests/lint/secret scan pass, health reports accurately, dashboard authenticates.

## 1A. Local model configuration and benchmark

If the deployment requires natural-language planning/autonomous task routing, install a local inference runtime and a model that fits the target PC. The repository CI does not install model weights or prove target-PC model quality.

Example using Ollama on Windows:

```powershell
# Install Ollama separately, then:
ollama pull qwen2.5:0.5b-instruct-q4_K_M
$env:MATRIX_MODEL_PROVIDER = "ollama"
$env:MATRIX_MODEL_NAME = "qwen2.5:0.5b-instruct-q4_K_M"
$env:MATRIX_MODEL_RAM_BUDGET_MB = "2500"
matrix serve
```

- Verify `GET /api/models` reports the provider/model as working.
- Run `matrix benchmark-model` and retain schema-valid plan rate, tool-selection accuracy, p50/p90 latency and observed memory.
- Confirm the chosen model remains within the machine's memory budget during concurrent MATRIX work.
- Keep cloud inference disabled unless the owner explicitly approves the provider, data handling and cost.

**Pass:** a local model is working and benchmark evidence is acceptable for the target PC, or the deployment explicitly scopes itself to deterministic/offline functions without claiming model-driven autonomy.

## 2. Windows startup, restart and recovery

```powershell
matrix startup install
matrix startup status
matrix backup
```

- Sign out and sign back in; verify the current-user, limited-privilege task starts MATRIX.
- Restart MATRIX and Windows; verify health, database integrity, scheduler and Telegram cursor recovery.
- Stop MATRIX during a harmless RECEIVED task and verify documented recovery behavior; do not use a destructive task for this test.
- Back up the database, restore into a separate test home, and verify `PRAGMA integrity_check` is `ok`.
- Test upgrade and rollback using a disposable copy before touching production data.

**Pass:** startup is reliable without SYSTEM/admin privilege; recovery does not replay uncertain non-idempotent side effects; backup restores.

## 2A. Containerized always-on Linux deployment

When deploying MATRIX on an always-on Linux host (server, VPS, home lab) rather than a desktop PC:

- Follow `docs/DEPLOYMENT.md` to deploy via Docker Compose (`docker compose up -d`).
- Confirm non-root execution (`USER matrix:matrix`, `UID 10001`).
- Verify container port is bound strictly to loopback (`127.0.0.1:8765`), never public `0.0.0.0`.
- Verify persistent volume `/data` retains database, audit logs, and Telegram offset across container restarts (`docker compose down && docker compose up -d`).
- Test online backup via `docker compose exec matrix python -m matrix_ai backup`.
- Check bridge status at `/api/telegram/status` and container health check via `docker compose ps`.

**Pass:** containerized daemon runs persistently, restarts safely, keeps data in volume, and runs Telegram without a desktop session.

## 3. Private Tailscale access

- Install/sign in to Tailscale on the PC and only the owner's approved devices.
- Configure restrictive tailnet ACLs before enabling Serve.
- On the PC:

```powershell
matrix remote-access status
matrix remote-access enable
matrix remote-access status
```

- From an approved device, open the tailnet HTTPS address and authenticate to MATRIX.
- From an unapproved tailnet identity/device, verify access is denied.
- Revoke the test device and verify it immediately loses MATRIX access.
- Confirm no Funnel, router port-forward or public listener was created.

**Pass:** only explicitly authorized tailnet devices can reach the loopback-proxied API; MATRIX bearer authentication remains required.

## 4. Telegram private chat and recovery

- Create a fresh bot using Telegram's official BotFather flow.
- Configure the bot token and numeric private chat ID only in the local MATRIX process environment/secret store. Never put either value in source control, issue comments or ChatGPT messages.
- Run `matrix telegram-preflight --send-test` to verify bot identity (`getMe`), absence of webhook conflicts, and outbound delivery.
- Start the bridge and verify only the configured private owner chat is accepted. Check `/api/telegram/status` or `matrix doctor` for bridge status (`healthy`, `consecutive_failures: 0`).
- Test ordinary task request/result, `/status`, `/help`, and the Telegram command menu (registered by MATRIX at startup).
- Send a harmless task, retain its full task ID, and verify `/tasks` lists it and `/task <task-id>` reports its state/result.
- Cancel a separate harmless long-running task with `/cancel <task-id>`; verify unrelated dashboard-created task IDs are rejected by Telegram task lookup/cancellation.
- Verify `/stop` cancels active tasks and blocks new work; `/resume` clears the stop for new work but does not restart tasks cancelled by `/stop`.
- Send a harmless task, restart MATRIX while it is pending, and verify both the durable update cursor and task-result notification watcher recover without creating duplicate tasks.
- Send a message from an unauthorized account or group and verify it is ignored and audited as `telegram.unauthorized_access` (`status="blocked"`).
- Test polling conflict (409) or temporary network failure; verify bridge backs off gracefully without crashing or thrashing.
- Verify Telegram cannot approve HIGH/CRITICAL actions and that emergency stop still blocks new work.
- Test bot token rotation/revocation and confirm no credential appears in logs/audit/dashboard.

**Pass:** private owner-only interaction, restart-safe task idempotency, emergency stop, and secret hygiene all work on the live bot.

## 5. Local voice

- Install `pip install -e ".[voice]"`.
- Pre-cache the selected faster-whisper model for offline use.
- Download a Piper ONNX voice and matching JSON sidecar from a trusted source; configure the local path.
- Test microphone permission, short English/Hindi clips, transcription accuracy, playback, mute, stop and barge-in.
- Verify barge-in stops playback promptly, cancellation releases the audio device, and raw audio is not retained.
- Test device sleep/resume and low-memory behavior.

**Pass:** usable microphone/speaker operation, no raw audio persistence, stop/mute respected. Record measured transcription latency and barge-in latency.

## 6. Android handset

- Retrieve the CI `matrix-android-apks` artifact while it is available and install the debug APK on a physical test handset.
- Pair with a one-time code from `matrix pair-code`.
- Test chat, task status, approval display, reconnect after process death/network change, local session clear and server-side device revocation.
- Confirm release network policy rejects cleartext HTTP.
- Confirm Android Keystore key/ciphertext are cleared on logout and backup restore fails closed.

**Pass:** pairing/reconnect/revocation/network checks pass on the physical device. A production release additionally requires an owner-controlled signing key, signed APK, signature verification and secure signing-key backup. CI's unsigned release APK is not distributable.

## 7. Provider integrations

For each provider the owner actually wants to enable:

- Create/use an official developer app and least-privilege scopes.
- Configure credentials locally; verify `matrix doctor` reports only configured/connected state without exposing secrets.
- Run read-only smoke tests first.
- Test one harmless write in a test/draft context, confirm MATRIX presents exact action arguments and requires owner approval, then verify the resulting provider state.
- Test expired/revoked credentials and provider rate limits.
- For WhatsApp inbound, configure public HTTPS callback verification, HMAC, sender allowlist and replay/idempotency checks. Do not expose the MATRIX API itself publicly.

**Pass:** each enabled adapter has a successful authenticated read and an explicitly approved write where supported. Disabled/unconfigured providers remain unavailable without breaking core MATRIX.

## 8. Operational and security sign-off

- Verify backup/restore and rollback from a clean backup.
- Define audit-log retention, local database protection/encryption, incident contact and recovery steps.
- Review Tailscale ACLs, Android signing custody, OAuth scopes and secret rotation.
- Keep `MATRIX_ALLOW_WEB_FETCH=false` until the connection-pinned transport has an independent security sign-off. The fix is **merged** (PR #75, closes #74) and main-branch CI is green; what remains is the reviewer sign-off recorded per `docs/SECURITY_REVIEW_WEB_FETCH.md`. The feature stays off by default regardless.
- Keep public Funnel/port forwarding disabled.
- Verify HIGH/CRITICAL exact-owner approval and emergency stop with harmless test actions.
- Record all remaining limitations and assign an owner before production rollout.

## Release decision

Production is approved only when every required gate for the intended deployment is evidenced. Optional integrations that are not part of the deployment may remain disabled and explicitly deferred. A CI pass alone is not production certification.
