# MATRIX AI — Production Deployment Guide

This guide describes how to deploy and operate MATRIX AI as a containerized, always-on service on Linux hosts (VPS, cloud instance, Raspberry Pi, home server, or local Docker).

---

## 1. Architecture & Security Posture

- **Process Model:** Fast single-process runtime hosting FastAPI API, background scheduler, Telegram long-polling bridge, and autonomous goal supervisor.
- **Network Isolation:** The container listens on internal port `8765`, which `docker-compose.yml` binds strictly to host loopback (`127.0.0.1:8765`). It is **never** exposed to `0.0.0.0` or the public internet by default.
- **Non-Root Execution:** Runs under dedicated system user `matrix` (`UID 10001`, `GID 10001`).
- **Data Persistence:** All runtime state (SQLite database, audit logs, paired device tokens, Telegram update watermark) lives in `/data` mounted to a named Docker volume (`matrix-data`).
- **Zero Inbound Ports Required for Telegram:** Telegram operation uses outbound HTTPS long-polling (`getUpdates`). No public webhook, public IP, or open router port is needed.
- **Fail-Closed Security Defaults:** `MATRIX_ALLOW_WEB_FETCH=false` and `MATRIX_ALLOW_CLOUD_MODELS=false` are enforced unless explicitly overridden.

---

## 2. Prerequisites

- Docker Engine 24.0+ and Docker Compose v2.
- A Linux host (Ubuntu 22.04/24.04 LTS, Debian 12, or Alpine recommended).
- Hardware: Minimum 1 vCPU, 1 GB RAM (2 GB+ recommended).
- Telegram bot credentials from [@BotFather](https://t.me/BotFather) and your numeric chat ID from [@userinfobot](https://t.me/userinfobot).

---

## 3. Quick Start (Docker Compose)

### Step 1: Clone the repository

```bash
git clone https://github.com/ayaanraza2008/matrix-ai.git
cd matrix-ai
```

### Step 2: Configure environment secrets

Copy the template and edit `.env`:

```bash
cp .env.example .env
chmod 600 .env
```

Set your Telegram credentials in `.env`:

```bash
MATRIX_TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
MATRIX_TELEGRAM_CHAT_ID="987654321"

# Set a strong random owner token for the API and dashboard
MATRIX_OWNER_TOKEN="your-secure-random-token-here"
```

### Step 3: Start the service

```bash
docker compose up -d --build
```

### Step 4: Verify health & Telegram bridge

Check container status and health:

```bash
docker compose ps
curl -s http://127.0.0.1:8765/api/health | jq .
```

Verify Telegram bridge health:

```bash
curl -s http://127.0.0.1:8765/api/telegram/status \
  -H "Authorization: Bearer your-secure-random-token-here" | jq .
```

Inspect logs:

```bash
docker compose logs -f matrix
```

Open Telegram, find your bot (`@matrixai_v1_bot`), and send `/status`.

---

## 4. Private Remote Access (Tailscale / Reverse Proxy)

To access the MATRIX web UI or Android companion securely from other devices without exposing ports to the public internet:

### Option A: Tailscale (Recommended)

1. Install Tailscale on the host:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
2. Enable Tailscale Serve to proxy traffic to loopback:
   ```bash
   sudo tailscale serve --https=443 127.0.0.1:8765
   ```
3. Access MATRIX at `https://<your-host>.<tailnet-name>.ts.net/`.

### Option B: Caddy Reverse Proxy with TLS

If running behind a domain with DNS pointed to your host:

```caddyfile
matrix.yourdomain.com {
    reverse_proxy 127.0.0.1:8765
    tls internal  # or automatic Let's Encrypt
}
```

---

## 5. Backup & Restore Procedures

### Online Backup

MATRIX supports SQLite online backups without interrupting active operations:

```bash
docker compose exec matrix python -m matrix_ai backup --out /data/backups/matrix-manual.db
```

### Volume Backup to Host

To copy the database out of the container volume:

```bash
docker run --rm -v matrix-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/matrix-backup-$(date +%Y%m%d%H%M%S).tar.gz -C /data .
```

### Restore Procedure

1. Stop the running container:
   ```bash
   docker compose down
   ```
2. Restore the volume:
   ```bash
   docker run --rm -v matrix-data:/data -v $(pwd):/backup alpine \
     sh -c "rm -rf /data/* && tar xzf /backup/matrix-backup-<timestamp>.tar.gz -C /data"
   ```
3. Restart the container:
   ```bash
   docker compose up -d
   ```

---

## 6. Upgrade & Rollback Procedures

### Upgrade to Latest Release

```bash
git fetch origin
git checkout main  # or target release tag, e.g. v0.2.0
docker compose build --no-cache
docker compose up -d
```

### Rollback to Previous Version

```bash
git checkout <previous-tag-or-commit>
docker compose build
docker compose up -d
```

Because database migrations are backwards-compatible and idempotent, existing SQLite data is safely preserved across upgrades and rollbacks.

---

## 7. Operational Diagnostics & Monitoring

| Check | Command |
|---|---|
| Container Status | `docker compose ps` |
| Application Health | `curl -s http://127.0.0.1:8765/api/health` |
| Telegram Bridge Status | `curl -s http://127.0.0.1:8765/api/telegram/status -H "Authorization: Bearer <token>"` |
| Live Logs | `docker compose logs -f --tail=100` |
| System Diagnostic Report | `docker compose exec matrix python -m matrix_ai doctor` |
| Database Integrity | `docker compose exec matrix sqlite3 /data/matrix.db "PRAGMA integrity_check;"` |

---

## 8. Real-World Production Certification Notice

> **Important Honesty Notice:**
> The existence of a Dockerfile, Compose file, or successful local container test constitutes **deployable software** (Category A — Engineering Completion). It is **not** proof that a remote server is continuously running 24/7 or production-certified (Category B — Real-World Production Certification).
> Production certification requires actual evidence from an active host running the service over time with verified owner-chat interactions.
