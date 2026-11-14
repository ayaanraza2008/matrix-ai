# MATRIX AI — VPS-Free Autonomous Cloud Architecture

## 1. Executive Summary

MATRIX AI is fully operable in an **autonomous, VPS-free, zero-hosting-cost cloud architecture**.

This design entirely eliminates:
- Paid VPS instances (AWS EC2, DigitalOcean Droplets, Hetzner, Linode).
- Always-on servers requiring monthly subscription fees.
- Personal PC uptime requirements (your local Windows PC can be completely turned off).
- Fragile ephemeral artifact storage.

---

## 2. System Architecture

```
                    ┌─────────────────────────┐
                    │      Telegram User      │
                    │   (@matrixai_v1_bot)    │
                    └────────────┬────────────┘
                                 │
                 ┌───────────────┴───────────────┐
                 │                               │
        (Real-time webhook)             (15-min scheduled polling)
                 │                               │
                 ▼                               │
      ┌─────────────────────┐                    │
      │  Cloudflare Worker  │                    │
      │  (Free Serverless)  │                    │
      └──────────┬──────────┘                    │
                 │ repository_dispatch           │
                 ▼                               ▼
      ┌───────────────────────────────────────────────┐
      │             GitHub Actions Runner             │
      │      (.github/workflows/cloud-scheduler.yml)  │
      │                                               │
      │  1. Pull State (GitStateSync from matrix-data)│
      │  2. Verify DB Integrity (PRAGMA integrity_chk)│
      │  3. Recover Pending Tasks (State Machine)     │
      │  4. Process Telegram Batch (sync_once)        │
      │  5. Execute & Drain Task Queue (Orchestrator) │
      │  6. Flush Delivery Reports (flush_reports)    │
      │  7. Checkpoint SQLite & Push (matrix-data)    │
      │  8. Publish Telemetry (GitHub Step Summary)   │
      └───────────────────────┬───────────────────────┘
                              │
                              ▼
      ┌───────────────────────────────────────────────┐
      │           Git Orphan State Branch             │
      │               (`matrix-data`)                 │
      │      - data/matrix.db (compacted SQLite)      │
      │      - Audit logs & idempotent watermarks     │
      │      - Zero secrets / private tokens committed│
      └───────────────────────────────────────────────┘
```

---

## 3. Core Architectural Components

### A. Execution Engine: Ephemeral GitHub Actions Runners
- **Scheduled Trigger (`schedule: cron '*/15 * * * *'`):** Periodic autonomous heartbeat executing every 15 minutes.
- **Manual Trigger (`workflow_dispatch`):** Run on-demand from GitHub web interface or via `gh workflow run cloud-scheduler.yml`.
- **Event-Driven Trigger (`repository_dispatch`):** Instantaneous execution triggered by external events (`matrix-task`, `telegram-webhook`, `cloud-step`).
- **Concurrency Isolation:** Bounded by `concurrency: { group: matrix-cloud-execution, cancel-in-progress: false }` to prevent overlapping runs or race conditions.

### B. Durable Persistence: Git Orphan Branch (`matrix-data`)
GitHub Actions runner filesystems are ephemeral (destroyed when the job finishes). MATRIX solves persistence without paid databases using **Git-backed atomic state sync**:
1. At job start, `matrix state-sync pull` fetches branch `matrix-data` and restores `data/matrix.db`.
2. The database is checked with `PRAGMA integrity_check`.
3. At job end, WAL checkpointing (`PRAGMA wal_checkpoint(TRUNCATE)`) merges pending writes into the main database.
4. `matrix state-sync push` uses isolated Git plumbing (`hash-object`, `update-index`, `commit-tree`, `update-ref`) to write directly to the `matrix-data` branch with `[skip ci]`.
5. Works without switching branches or dirtying the working directory.

### C. Telegram Remote Control Center
The Telegram bridge operates in three configurable modes (`MATRIX_TELEGRAM_MODE`):
- **`polling` (default):** Uses Telegram `getUpdates` API. In local daemon mode, it runs continuous long-polling. In cloud-scheduler mode, `sync_once()` pulls a single batch and advances the offset watermark.
- **`webhook`:** Real-time push via Cloudflare Worker. Updates arrive via GitHub `repository_dispatch` and are processed immediately with `matrix cloud-step --ingest-update`. Scheduled 15-minute cron runs in webhook mode recover tasks, drain queue, and flush reports, but **strictly avoid calling `getUpdates`**, eliminating HTTP 409 Conflict.
- **`disabled`:** Turns off Telegram bridge functionality completely.

#### Lifecycle Differentiation:
1. `1. Command received: /run <instruction>`
2. `2. Task queued: ID <task_id>`
3. `3. Task executed: <result>`
4. `4. Task failed: <error>`
5. `5. Task awaiting owner authorization in dashboard: <detail>`

#### Supported Commands:
- `/start` — Connect to MATRIX.
- `/help` — Display command documentation.
- `/status` — View system state, active tasks, emergency stop status.
- `/health` — Inspect database, model, and memory health.
- `/tasks` — List recent Telegram-submitted tasks.
- `/run <instruction>` — Submit a natural-language autonomous task.
- `/cancel <task-id>` — Cancel an active task.
- `/stop` — Immediate emergency stop.
- `/resume` — Clear emergency stop and resume processing.

#### Security & Deduplication:
- Rejects any update where chat ID or sender ID does not match the configured `MATRIX_TELEGRAM_CHAT_ID`.
- Update offset is tracked durably in SQLite with idempotent deduplication; duplicate updates cannot trigger duplicate tool executions.

### D. Real-Time Relay: Cloudflare Worker (Zero Cost)
For instantaneous response times from Telegram without running a VPS or personal PC:
- Cloudflare Workers Free Tier provides **100,000 requests per day at $0 cost**.
- The script `deploy/serverless/cloudflare_telegram_webhook_worker.js` validates Telegram's secret token, enforces fail-closed authentication, deduplicates update IDs, and fires GitHub `repository_dispatch`.
- **Reliable Retry Guarantee (Two-Phase Dispatch):** The Worker only marks an update as dispatched **after** GitHub returns HTTP 204. If GitHub is temporarily busy or returns an error, the Worker returns HTTP 502 to Telegram without recording the update in the cache. This ensures Telegram's automated webhook retry policy is never suppressed, and commands are never silently lost.
- **Durable Idempotency:** The worker supports optional Cloudflare KV binding (`MATRIX_WEBHOOK_KV`) for edge deduplication across worldwide data centers. The ultimate source of truth remains MATRIX's SQLite atomic idempotency table (`matrix-data` branch).
- Execution starts in GitHub Actions runner within ~20-30 seconds of sending a message in Telegram.

#### Cloudflare Worker Deployment Guide

##### Step 1: Deploy Worker (Via Cloudflare Dashboard)
1. Log in to [Cloudflare Dashboard](https://dash.cloudflare.com) -> **Compute (Workers & Pages)** -> **Create Application** -> **Create Worker**.
2. Name the Worker (e.g. `matrix-telegram-relay`).
3. Click **Deploy**.
4. Click **Edit code**, replace the template with the contents of `deploy/serverless/cloudflare_telegram_webhook_worker.js`, and click **Deploy**.
5. Note your Worker URL (e.g. `https://matrix-telegram-relay.<your-subdomain>.workers.dev`).

*(Alternative: Deploy via Wrangler CLI)*
```bash
npx wrangler deploy deploy/serverless/cloudflare_telegram_webhook_worker.js --name matrix-telegram-relay --compatibility-date 2024-01-01
```

##### Step 2: Configure Worker Secrets (Mandatory — Fail-Closed)
In Cloudflare Dashboard -> **Settings** -> **Variables and Secrets**, add the following 4 secrets:
1. `TELEGRAM_SECRET_TOKEN`: A secret random token string (e.g. generate via `openssl rand -hex 24`).
2. `TELEGRAM_OWNER_ID`: Your numeric Telegram user ID (e.g. `123456789`).
3. `GITHUB_TOKEN`: A GitHub Personal Access Token (classic `repo` scope or fine-grained with `Contents: write` and `Actions: write`).
4. `GITHUB_REPO`: `ayaanraza2008/matrix-ai`

##### Step 3: Register Telegram Webhook
Run the MATRIX CLI command or `curl` to point your bot to the Cloudflare Worker:
```bash
# Using MATRIX CLI
matrix telegram-webhook set \
  --url "https://matrix-telegram-relay.<your-subdomain>.workers.dev" \
  --secret-token "<YOUR_TELEGRAM_SECRET_TOKEN>" \
  --drop-pending

# Or using curl directly:
curl -s -X POST "https://api.telegram.org/bot<MATRIX_TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://matrix-telegram-relay.<your-subdomain>.workers.dev" \
  -d "secret_token=<YOUR_TELEGRAM_SECRET_TOKEN>" \
  -d "drop_pending_updates=true"
```

Verify webhook registration:
```bash
matrix telegram-webhook status
```

##### Step 4: Configure GitHub Repository Settings
On GitHub (`https://github.com/ayaanraza2008/matrix-ai/settings`):
- **Variables (`settings/variables/actions`):**
  - `MATRIX_TELEGRAM_MODE`: `webhook`
- **Secrets (`settings/secrets/actions` or in environment `matrix TG`):**
  - `MATRIX_TELEGRAM_BOT_TOKEN`: Your Telegram Bot API token.
  - `MATRIX_TELEGRAM_CHAT_ID`: Your numeric Telegram ID.
  - `MATRIX_TELEGRAM_WEBHOOK_SECRET`: `<YOUR_TELEGRAM_SECRET_TOKEN>` (matches the Cloudflare Worker secret).

##### Step 5: Webhook Removal / Polling Fallback (If Needed)
If you wish to return to polling mode at any time:
```bash
# 1. Delete webhook from Telegram
matrix telegram-webhook delete --drop-pending

# 2. Update GitHub Secret
# Set MATRIX_TELEGRAM_MODE=polling
```

---

## 4. Free-Tier AI Model Hierarchy & Fallback

MATRIX supports multiple zero-cost AI model backends:
1. **Google Gemini Free Tier (`gemini-1.5-flash` / `gemini-2.0-flash`):**
   - High intelligence and generous free rate limits for developers.
   - Configured via `MATRIX_MODEL_PROVIDER=gemini` and `MATRIX_GEMINI_API_KEY`.
2. **FreeLLMAPI:**
   - Free OpenAI-compatible community router.
   - Configured via `MATRIX_MODEL_PROVIDER=freellmapi`.
3. **Local Models (Ollama / llama.cpp):**
   - Supported when running on local devices.
4. **Deterministic Fallback (`none`):**
   - MATRIX core features (file search, memory management, health diagnostics, git logging, system monitoring) run deterministically without requiring an LLM.

Fallback routing is configurable with `MATRIX_MODEL_FALLBACK_PROVIDER`. If the primary model encounters rate limits (HTTP 429) or transient outages (HTTP 5xx), the orchestrator automatically invokes the fallback provider with structured audit logging.

---

## 5. Security & Isolation

- **Zero Secrets Committed:** `.gitignore` excludes all `.env`, `.db`, credentials, and keys. Git state sync commits only the verified `data/matrix.db`.
- **Owner-Only Authentication:** All Telegram interactions and API endpoints require authenticated tokens.
- **Approval Boundaries:** High-risk actions (file deletion outside workspace, credential writes) enter `APPROVAL_REQUIRED` and pause execution until explicitly approved.
- **Replay Protection:** Telegram `update_id` is tracked in SQLite with idempotent deduplication; duplicate updates cannot trigger duplicate tool executions.

---

## 6. GitHub Actions Quota & Budget Safety

| Metric | Free Tier Allowance | MATRIX AI Usage |
|---|---|---|
| **Monthly Runner Minutes** | 2,000 min/month (Private repos)<br>Unlimited (Public repos) | ~1,200 min/month (4 runs/hr × 25s) |
| **Storage Quota** | 500 MB (Git LFS / branch) | ~15-30 MB (`matrix.db` compressed) |
| **Cloudflare Worker** | 100,000 requests/day | ~50-200 requests/day |
| **Total Monthly Cost** | **$0.00** | **$0.00** |

---

## 7. Setup & Verification Commands

### Test Cloud-Step Locally
```bash
# Run a single autonomous cycle
python -m matrix_ai.cli cloud-step --no-pull-state --no-push-state --markdown
```

### Inspect State Sync Status
```bash
# Check remote state branch and local database integrity
python -m matrix_ai.cli state-sync status
```

### Trigger Cloud Scheduler on GitHub
```bash
# Trigger immediate cloud execution via GitHub CLI
gh workflow run cloud-scheduler.yml
```
