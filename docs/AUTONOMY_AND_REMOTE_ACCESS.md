# Always-available MATRIX and safe autonomy

This guide adds explicit, free-first options to the local MATRIX service: a Windows
per-user startup task, private Tailscale Serve, and persistent core-health monitoring.
It does not turn MATRIX into a cloud service and cannot make a powered-off PC reachable.

## 1. Keep MATRIX running after sign-in

After installing MATRIX into the Python environment you intend to keep:

```powershell
python -m pip install -e ".[dev]"
matrix startup install
matrix startup status
```

The scheduled task runs `python -m matrix_ai serve` at the current Windows
user's interactive logon, at **LIMITED** privilege. It does not run as SYSTEM,
does not request administrator rights and does not start before the user signs in.
Remove it with `matrix startup uninstall`.

Keep the PC powered on, connected to the internet and the Windows account signed
in for remote use. Windows sleep, shutdown, network loss or a stopped Tailscale
daemon make MATRIX unavailable. Do not configure a public port-forward.

## 2. Private remote access (Tailscale Serve)

Install Tailscale on the MATRIX PC and on each device you want to use. Sign them
into the same private tailnet and set restrictive Tailscale ACLs. Then on the
MATRIX PC run:

```powershell
matrix remote-access status
matrix remote-access enable
matrix remote-access status
```

The helper configures only `tailscale serve --bg 127.0.0.1:8765`. MATRIX remains
bound to loopback; Tailscale terminates HTTPS and proxies to the local API. The
result is available within the tailnet only. MATRIX never invokes Tailscale
Funnel and refuses to overwrite a different existing Serve route. Disable its
route with `matrix remote-access disable`.

You must still authenticate to MATRIX with the owner/device bearer token. Tailscale
Serve is a private network path, not a replacement for application authentication.
The tailnet ACL determines which enrolled devices/users can reach the service.
The standard Tailscale Serve setup may prompt you to enable HTTPS certificates in
your tailnet.

## 3. What scheduled autonomy means

MATRIX scheduled jobs are explicit owner-created goals. The existing API/CLI can
create and manage them through `/api/jobs`; the scheduler then asks the local
planner and selected specialist workers to work on the prompt at its interval.
A job's autonomy level is capped by `MATRIX_AUTONOMY_LEVEL`:

- **L0:** observe; no tool side effects.
- **L1:** prepare; plan and collect information, but insufficient for medium-risk execution.
- **L2:** safe execution; eligible low/medium-risk, allowlisted tools can run inside the job's granted scopes.

The scheduler now respects the job's requested level up to the global configuration
ceiling. It does **not** weaken the policy engine: HIGH/CRITICAL calls still need
exact, time-limited owner approval; missing scopes, emergency stop, schema failures,
tool timeouts, verification failures and task deadlines continue to block or fail
work. MATRIX must not grant itself new permissions, approve its own requests,
invent credentials or silently repeat a non-idempotent action.

Owner goals have a persistent lifecycle through the authenticated API:

- `GET /api/jobs` — list saved goals and their next/last run information.
- `PUT /api/jobs/{id}` — update the name, prompt, interval, autonomy level or enabled state.
- `PATCH /api/jobs/{id}` — pause or resume a goal.
- `POST /api/jobs/{id}/run` — request an immediate run.
- `DELETE /api/jobs/{id}` — permanently remove a saved goal.

Edits, pauses/resumes and deletions are recorded in the audit log. Resuming a
paused goal schedules its next run one full interval from resume time rather than
immediately replaying an overdue run. Immediate runs are capped at the configured
global autonomy ceiling and the scheduled-job maximum. Deletion removes the
recurring definition; it does not erase task history or audit records already
created by earlier runs.

Example owner-created recurring goal (authenticated request):

```http
POST /api/jobs
Authorization: Bearer <owner-token>
Content-Type: application/json

{
  "name": "Daily project review",
  "prompt": "Review the approved MATRIX workspace and scheduled task health. Summarize blockers, propose next steps, and do not publish, delete, purchase, or change account/security settings.",
  "interval_seconds": 86400,
  "autonomy_level": 2,
  "enabled": true
}
```

Do not use broad prompts such as "do anything necessary". Define the workspace,
allowed actions, cadence and forbidden actions. Keep emergency stop available.

## 4. Persistent health monitoring and Telegram alerts

The background scheduler checks the local database integrity, model availability
and emergency-stop state every minute. It persists the last health state in SQLite
and records transitions in the audit log. A degraded state or recovery is recorded
once per transition, rather than generating an alert every poll.

Telegram alerts are optional. Configure a bot and the one destination chat you
control using environment variables before starting MATRIX:

```powershell
$env:MATRIX_TELEGRAM_BOT_TOKEN = "<your-bot-token>"
$env:MATRIX_TELEGRAM_CHAT_ID = "<your-chat-id>"
matrix serve
```

The monitor sends only the state and component statuses (database, model and
emergency stop). It never includes task prompts, file contents, owner tokens or
integration credentials. If Telegram is not configured, health transitions are
still persisted and audited locally. A failed Telegram delivery is audited and
does not stop the scheduler. Do not commit bot credentials to the repository.

This monitor cannot send an alert while the host is powered off, asleep, offline,
or MATRIX is not running. It currently checks core service health; it does not
claim to monitor Windows hardware, Tailscale reachability, or every third-party
integration.

## 5. Important limits

- Tailscale is an additional private-network dependency; MATRIX core, model, memory
  and scheduled jobs remain self-hosted.
- The machine must be online and MATRIX must be running. No code can reach a PC
  that is powered off or disconnected.
- PSTN numbers and carrier minutes are not free. Local SIP/WebRTC between devices
  on your network is a different capability and remains a separate milestone.
- Voice engines are optional. Real microphone/speaker, camera and Android hardware
  validation must still be done on the target devices.
- This feature does not authorize MATRIX to change its own permissions, bypass
  authentication, disable the emergency stop or publish content without the
  configured tool/policy/approval checks.

## 6. Verification

```powershell
matrix doctor
matrix startup status
matrix remote-access status
python -m pytest -q
python -m ruff check .
```
