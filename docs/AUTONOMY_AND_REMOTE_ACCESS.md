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

Optional timezone-aware quiet hours apply only to routine recovery notifications.
Set both variables to enable the window; times use 24-hour `HH:MM` format:

```powershell
$env:MATRIX_NOTIFICATION_QUIET_HOURS_START = "22:00"
$env:MATRIX_NOTIFICATION_QUIET_HOURS_END = "07:00"
$env:MATRIX_NOTIFICATION_TIMEZONE = "Asia/Kolkata"
```

The quiet window supports overnight ranges. Degraded-state alerts and a newly
activated emergency stop bypass quiet hours. Recovery messages are queued until
quiet hours end; failed deliveries remain queued and are retried on later health
checks. Pending state is stored locally in SQLite. The authenticated
`GET /api/automation/status` response reports the active routing configuration
without exposing the bot token or chat credentials. Quiet hours are disabled by
default.

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


## 5. Authenticated in-app notification inbox

Health transitions are also written to a bounded local inbox, even when Telegram is not configured. The inbox stores at most 100 recent events and exposes only authenticated owner/device API routes:

- `GET /api/notifications?limit=50&unread_only=false` — newest notifications first, plus unread count.
- `POST /api/notifications/{id}/read` — mark one notification as read; the action is audited.

The inbox includes the core health component summary only. It does not include task prompts, file contents, tokens or integration credentials. Critical emergency-stop events are labelled critical. The inbox is local persistence, not a substitute for an external alert: it cannot notify a device while MATRIX is offline, and push delivery remains optional.


### Dashboard inbox, preferences and daily digest

The dashboard renders the authenticated local notification inbox, unread count, severity, event type and read acknowledgement. Owner preferences are available through `GET/PUT /api/notifications/preferences`; the recovery preference applies across the inbox and enabled push channels, while the daily digest remains an in-app item. Degraded-state and emergency-stop alerts are not suppressible.

The scheduler creates at most one daily digest per local calendar day, on its first tick at or after 09:00 in `MATRIX_NOTIFICATION_TIMEZONE`. The digest contains aggregate counts for recent tasks and the emergency-stop state, not task prompts or private task content. It is persisted in the same bounded inbox. The scheduler must be running for the digest to be generated; it is not an external push guarantee.


### Optional signed webhook notifications

MATRIX can deliver health transitions to an owner-configured HTTPS webhook in addition to Telegram. Configure all three environment variables together:

- `MATRIX_NOTIFICATION_WEBHOOK_URL` — HTTPS endpoint, default port only, no URL credentials, query or fragment.
- `MATRIX_NOTIFICATION_WEBHOOK_SECRET` — signing secret used for HMAC-SHA256.
- `MATRIX_NOTIFICATION_WEBHOOK_ALLOWED_HOSTS` — comma-separated exact host allowlist; local/private IPs and local hostnames are rejected.

Each request includes `X-MATRIX-Event-ID` and `X-MATRIX-Signature: sha256=...`. The JSON body includes the stable event ID, event type, severity and a limited health component summary. Receivers should verify the HMAC over the exact raw request body and deduplicate by event ID. MATRIX retries failed channels independently and remembers successful channels so a retry does not resend an already-confirmed Telegram or webhook delivery. Redirects are disabled. No webhook destination or signing secret is exposed in public settings.


Webhook delivery is an optional second push channel beside Telegram. Per-event queue records remember which channels have confirmed delivery; retries target only channels still outstanding. This avoids duplicating successful sends when one provider is temporarily unavailable.
