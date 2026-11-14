# MATRIX AI — Tailscale Private Remote Access & Security Guide

This document specifies the architecture, Tailscale Access Control Lists (ACLs), deployment configuration, and verification procedures for accessing MATRIX AI securely across a private tailnet.

---

## 1. Security Architecture & Threat Model

- **Loopback Enforcement:** MATRIX binds strictly to `127.0.0.1:8765` by default. It is never exposed directly to `0.0.0.0` or public network interfaces.
- **Private Tailnet Only:** Tailscale Serve acts as an authenticated HTTPS proxy terminating TLS with a MagicDNS certificate (e.g., `https://<node>.your-tailnet.ts.net`).
- **Funnel Prohibition:** Tailscale Funnel (public internet ingress) is **prohibited** by MATRIX security policy. No traffic enters without tailnet cryptographic node-identity and user authorization.
- **Defense in Depth:** Even within the tailnet, all API endpoints require valid MATRIX Bearer authentication (`MATRIX_OWNER_TOKEN` or device session token). Tailnet transport does not bypass application-level authorization.

---

## 2. Recommended Tailscale ACL Configuration

Paste this configuration into the [Tailscale Admin Console ACL Editor](https://login.tailscale.com/admin/acls):

```json
{
  "tagOwners": {
    "tag:matrix-server": ["group:owners", "autogroup:admin"],
    "tag:matrix-client": ["group:owners", "autogroup:admin"]
  },
  "groups": {
    "group:owners": ["your-email@example.com"]
  },
  "acls": [
    // Allow owner devices and designated clients to reach the MATRIX server on HTTPS (443) and local API (8765)
    {
      "action": "accept",
      "src": ["group:owners", "tag:matrix-client"],
      "dst": ["tag:matrix-server:443", "tag:matrix-server:8765"]
    },
    // Allow the server itself to reach Tailscale coordination and DNS
    {
      "action": "accept",
      "src": ["tag:matrix-server"],
      "dst": ["autogroup:internet:*"]
    }
  ],
  "nodeAttrs": [
    {
      "target": ["tag:matrix-server"],
      "attr": ["serve"]
    }
  ],
  "tests": [
    {
      "src": "your-email@example.com",
      "accept": ["tag:matrix-server:443"],
      "deny": ["tag:matrix-server:22"]
    }
  ]
}
```

---

## 3. Host Enrollment & Setup

### On the MATRIX Host (Windows)

1. Download and install Tailscale for Windows:
   ```powershell
   winget install Tailscale.Tailscale
   ```
2. Authenticate the host with the `tag:matrix-server` tag:
   ```powershell
   tailscale up --operator=$env:USERNAME --accept-routes=false
   ```
3. Enable private HTTPS proxy to the loopback MATRIX daemon:
   ```powershell
   matrix remote-access enable
   ```
   *(Or alternatively via native CLI: `tailscale serve --https=443 127.0.0.1:8765`)*

4. Check status and retrieve your private URL:
   ```powershell
   matrix remote-access status
   ```

### On the MATRIX Host (Docker / Linux Container)

When running in containerized environments, Tailscale can run either on the host OS or as a sidecar container in `docker-compose.yml`:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --accept-routes=false
sudo tailscale serve --https=443 127.0.0.1:8765
```

---

## 4. Connection Diagnostics & Health Verification

### 1. Verify Node Connectivity
From an authorized tailnet client (laptop or mobile device):
```bash
tailscale ping <matrix-host-tailscale-ip-or-name>
```

### 2. Verify Private HTTPS & Health Check
```bash
curl -fsS https://<node>.<tailnet>.ts.net/api/health
```
Expected output:
```json
{
  "status": "ok",
  "version": "0.1.0",
  "database": "ok",
  "emergency_stop": false
}
```

### 3. Verify Unauthorized Device Denial
Attempting connection from a device not matching `group:owners` or `tag:matrix-client` must be rejected at the WireGuard transport layer (connection timeout / route denied).

### 4. Verify Revocation
If a device is lost or compromised, revoke its node key in the Tailscale Admin Console. The revoked device immediately loses cryptographic network access without needing MATRIX restart.

---

## 5. Troubleshooting Matrix Remote Access

| Symptom | Cause | Resolution |
|---|---|---|
| `tailscale: command not found` | Tailscale CLI not in PATH | Ensure `C:\Program Files\Tailscale` is on PATH or set `MATRIX_TAILSCALE_BIN` |
| `HTTP 401 Unauthorized` | Missing MATRIX Bearer Token | Pass `-H "Authorization: Bearer <token>"` with request |
| `Connection Refused` | MATRIX daemon not running | Run `matrix serve` or check `docker compose ps` |
| `SSL Certificate Error` | HTTPS cert not yet provisioned | Run `tailscale cert <node>.ts.net` to trigger certificate issuance |
| `Funnel Warning` | Public Funnel attempted | Disable Funnel immediately; MATRIX only permits private Serve |
