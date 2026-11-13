## MATRIX AI — Owner Action Packets for Gate Unblocking

### Gate 1 — Independent Security Review

**Current status:** BLOCKED — No eligible independent reviewer reachable through connected tools.

**Exact blocker:** Repository has one collaborator (owner account). GitHub Copilot code review is not enabled/available on this repo's plan, and enabling it would require a paid subscription, which is outside this session's authorization. The review package `docs/SECURITY_REVIEW_WEB_FETCH.md` is complete with 15-checklist, threat model, and sign-off template, but requires a reviewer who did not author the change.

**Minimal owner action:** 
1. Review the security review package at `docs/SECURITY_REVIEW_WEB_FETCH.md`.
2. Either:
   a. Identify and contact an independent security reviewer with demonstrated experience in Python networking, SSRF, DNS rebinding, socket/TLS security, or application security, and request they review the implementation at commit 80c3df0 against the 15-checklist.
   b. Or, open a review on a no-op documentation PR and record the decision on issue #73.
3. Run: `python -m pytest tests/test_web_fetch_security.py tests/test_web_and_providers.py -q` to verify all security tests pass.
4. Record: Reviewer identity (or explicit anonymity), date, decision (approved / changes requested), and any findings with severity.

**Expected result after action:** Reviewer records PASS decision with identity and date, `MATRIX_ALLOW_WEB_FETCH=false` gate cleared, production certification possible for Gate 1.

---

### Gate 2 — Telegram Live Acceptance

**Current status:** BLOCKED — `MATRIX_TELEGRAM_BOT_TOKEN` and `MATRIX_TELEGRAM_CHAT_ID` both None in config.

**Exact blocker:** Bot credentials not configured. No Telegram bot or private chat ID has been set up.

**Minimal owner action:**
1. Follow `TELEGRAM_SETUP_GUIDE.md`:
   a. Create a bot via @BotFather: `/newbot`, choose a name and username ending in "bot".
   b. Obtain the bot token from BotFather's response.
   c. Get your numeric chat ID: message @userinfobot in Telegram, or use `https://api.telegram.org/bot<TOKEN>/getUpdates` and find `"chat":{"id":<NUMBER>,"type":"private"}`.
2. Set environment variables (never commit to source control):
   ```powershell
   $env:MATRIX_TELEGRAM_BOT_TOKEN="<YOUR_BOT_TOKEN>"
   $env:MATRIX_TELEGRAM_CHAT_ID="<YOUR_CHAT_ID>"
   ```
3. Run preflight verification:
   ```powershell
   matrix telegram-preflight
   ```
4. Send test message:
   ```powershell
   matrix telegram-preflight --send-test
   ```
   A message "MATRIX telegram-preflight test message." should arrive in your private chat.
5. Start the bridge and test `/start`, `/help`, `/status`, `/stop`.
6. Submit a harmless task and verify result delivery.

**Expected result after action:** Bot identity verified, owner-only chat accepted, test message delivered, bridge starts, commands work, harmless task submits, restart recovery works, emergency stop blocks new work.

---

### Gate 3 — Windows Host Acceptance

**Current status:** BLOCKED — Physical Windows PC not available in connected execution environment.

**Exact blocker:** GitHub Actions, remote runners, and this execution environment cannot perform physical-device acceptance tests (voice, startup install, sleep/wake, reboot, microphone/speaker).

**Minimal owner action:**
1. Execute `docs/PRODUCTION_ACCEPTANCE_RUNBOOK.md` §1–§2, §5 on the target Windows PC.
2. The runner script `scripts/windows_final_acceptance.ps1` is prepared for owner execution.
3. The script should:
   - Install/check prerequisites (Python 3.11/3.12, Git, submodules).
   - Run `matrix doctor` and verify sanitized output.
   - Run `python -m pytest -q` (core tests).
   - Run `ruff check . --exclude agency-agents` and verify all checks pass.
   - Run `python -m matrix_ai security scan .` and verify clean.
   - Check startup task status and database integrity.
   - Create an isolated backup and verify `PRAGMA integrity_check` = `ok`.
   - Test backup/restore in an isolated test home.
   - Capture sanitized logs and produce a machine-readable JSON evidence report.
   - Clearly identify physical tests that cannot be automated (microphone/speaker, sleep/wake).
4. Run the script on the target PC and submit the evidence report.

**Expected result after action:** Clean install, all tests/lint/secret scan pass, health reports accurately, dashboard authenticates, startup reliable without admin privilege, recovery does not replay non-idempotent side effects.

---

### Gate 4 — Tailscale Acceptance

**Current status:** BLOCKED — No authorized tailnet access in connected execution environment.

**Exact blocker:** Tailscale Serve configuration and ACL validation require enrollment in the owner's actual tailnet.

**Minimal owner action:**
1. When the authorized tailnet is available:
   a. Enroll the MATRIX host: `tailscale up --advertise-routes=<subnet>`
   b. Enroll an authorized device (install Tailscale, sign in with owner account).
   c. Verify `matrix remote-access status` — should show installed: true, connected: true.
   d. Run `matrix remote-access enable` — confirms safe configuration.
   e. From an approved remote device, open the tailnet HTTPS address and authenticate to MATRIX.
   f. From an unapproved tailnet identity/device, verify access is denied (401/unauthenticated).
   g. Revoke the test device and verify it immediately loses MATRIX access.
   h. Inspect ACLs via `tailscale acls` — confirm only approved devices have access.
   i. Confirm no Funnel, router port-forward, or public listener was created.
   j. Confirm `matrix remote-access status` shows `serve_enabled: false` or safely configured.
2. If the tailnet is not currently available, prepare the onboarding flow using the owner's Tailscale account through the official Tailscale interface, and document the exact steps for later execution.

**Expected result after action:** Only explicitly authorized tailnet devices can reach the loopback-proxied API; MATRIX bearer authentication remains required; no Funnel or port-forwarding; revoked device immediately loses access.

---

### Gate 5 — Android Device + Signing

**Current status:** BLOCKED — No physical Android handset and no owner-controlled signing keys in connected environment.

**Exact blocker:** Physical handset required for acceptance; owner-controlled signing keys needed for release APK verification.

**Minimal owner action:**
1. **Debug APK testing:**
   a. Retrieve the latest CI `matrix-android-apks` artifact.
   b. Install the debug APK on an authorized Android handset via sideload or USB debugging.
   c. Pair using `matrix pair-code` — obtain one-time pairing code from MATRIX dashboard.
   d. Submit a harmless task and verify result.
   e. Test network interruption/reconnect.
   f. Restart the app and verify reconnect.
   g. Restart MATRIX and verify pending-update recovery.
   h. Clear the local session and verify server-side device revocation.
   i. Test HTTPS enforcement (settings → should show secure connection).
   j. Check crash/logs without exposing secrets.

2. **Release signing:**
   a. Inspect approved GitHub Actions secrets for existing signing configuration.
   b. If a protected signing key/certificate exists:
      - Build the signed release APK using the authorized procedure.
      - Verify the certificate fingerprint with: `keytool -list -keystore <path-to-keystore>`.
      - Verify the signature: `apksigner verify --print-certs <release-apk>`.
      - Test installation and upgrade on the handset.
      - Document signing-key custody and secure backup procedure.
   c. If no signing key exists, prepare an owner-controlled key generation procedure:
      - Generate key pair on the owner's trusted machine using `keytool -genkeypair`.
      - Store the keystore file in a secure location (not in source control).
      - Sign the release APK and verify the certificate.
      - Test installation and upgrade.
      - Document the secure signing-key custody procedure.

**Expected result after action:** Debug APK installs and pairs; task operations work; network interruption/recovery works; app restart works; session clear works; server-side revocation rejects reconnect; release APK installs and signature verifies.

---

### Gate 6 — Provider Connections

**Current status:** BLOCKED — No OAuth accounts connected for any provider.

**Exact blocker:** No OAuth accounts or credentials configured for any of the required providers.

**Minimal owner action — per provider:**

**GitHub:**
1. Generate a fine-grained personal access token or classic token with required scopes.
2. Set `MATRIX_GITHUB_TOKEN` in the local environment/secret store (not in source control).
3. Run `matrix doctor` — should show GitHub: configured/connected with limited details.
4. Run read-only smoke test: the GitHub adapter should report repository/metadata without exposing secrets.

**YouTube:**
1. Create a Google Cloud project and set up OAuth 2.0 Desktop client.
2. Download `client_secret.json` and set `MATRIX_YOUTUBE_CLIENT_SECRETS` to its path (protected local storage).
3. Run the OAuth flow: `matrix youtube-auth` (or equivalent authorized flow).
4. Set `MATRIX_YOUTUBE_CREDENTIAL_PATH` or use the OS credential manager.
5. Run `matrix doctor` — should show YouTube: configured.
6. Run read-only smoke test: verify channel metadata without publishing.

**Instagram/Meta:**
1. Create a Meta Developer app for a Professional (Business/Creator) account.
2. Set `MATRIX_INSTAGRAM_ACCESS_TOKEN`, `MATRIX_INSTAGRAM_USER_ID`, and `MATRIX_META_GRAPH_VERSION` in the local environment (protected, not in source).
3. Run `matrix doctor` — should show Instagram: configured.
4. Run read-only smoke test: verify comment metadata without publishing posts.

**LinkedIn:**
1. Create a LinkedIn developer app with `w_member_social` access.
2. Set `MATRIX_LINKEDIN_ACCESS_TOKEN` and `MATRIX_LINKEDIN_AUTHOR_URN` in the local environment.
3. Run `matrix doctor` — should show LinkedIn: configured.
4. Run read-only smoke test: verify author information without publishing posts.

**WhatsApp:**
1. Create a Meta Business Account and obtain:
   - `MATRIX_WHATSAPP_ACCESS_TOKEN`
   - `MATRIX_WHATSAPP_PHONE_NUMBER_ID`
   - `MATRIX_META_GRAPH_VERSION`
2. Set all three secrets in the local environment (protected, not in source).
3. Configure webhook verification: set `MATRIX_WHATSAPP_APP_SECRET` and `MATRIX_WHATSAPP_ALLOWED_SENDERS` (semicolon-separated list of allowed sender phone numbers).
4. Run `matrix doctor` — should show WhatsApp: configured.
5. Run read-only smoke test: verify webhook payload validation without sending messages.

**Expected result after action:** Each enabled adapter reports configured/connected status in `matrix doctor`; authenticated read operations succeed; provider rate limits handled; disconnect does not crash core MATRIX.

---

### Cross-Gate — Backup and Recovery

**Current status:** PASS in-repo

**Evidence:** All automated checks green: backup creation, integrity validation, truncated backup rejection, secret exclusion, consistent snapshot restore, and failed-upgrade recovery (PR #79) — regression tested in CI.

**Live rehearsal:** Belongs to gate 3 (Windows host acceptance).

**Owner action (if wanting to verify live):**
1. Run backup: `matrix backup --out <path>`.
2. Verify integrity: `PRAGMA integrity_check` = `ok`.
3. Restore into isolated home: copy backup to temporary directory, open with SQLite, run `PRAGMA integrity_check`.
4. Test failed-upgrade recovery by simulating crash-during-migrate (the regression test in PR #79 provides the automated equivalent).

---
**Summary — Owner Action Priority:**

| Gate | Required Action | Why It Unblocks |
|------|----------------|-----------------|
| 1 | Review security package & obtain independent reviewer | Clears the fail-closed `MATRIX_ALLOW_WEB_FETCH=false` gate |
| 2 | Configure Telegram bot + chat ID | Enables live preflight and bridge acceptance |
| 3 | Run acceptance script on target Windows PC | Physical host acceptance cannot be remote-substitute |
| 4 | Enroll in authorized tailnet + test access/revocation | Tailscale private-access and revocation tests |
| 5 | Deploy debug APK to handset + sign release APK | Physical Android acceptance + signing verification |
| 6 | Configure OAuth accounts for each provider | Provider authenticated smoke tests |

**No further cloud-executable engineering work is open** for these gates. Every remaining step requires a specific owner credential, device, account authorization, or independent human reviewer that no connected tool can provide.

**Issue #73 stays open** until all mandatory gates produce real-world evidence.