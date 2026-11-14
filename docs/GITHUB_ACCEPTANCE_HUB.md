# MATRIX GitHub Acceptance Hub

This is the repository-native acceptance entry point. No local clone, CMD, PowerShell, or OpenCode checkout is required to run these GitHub-hosted checks.

## Run it

1. Merge the pull request that adds this workflow into `main`.
2. Open **Actions → MATRIX Acceptance Hub**:
   https://github.com/ayaanraza2008/matrix-ai/actions/workflows/acceptance.yml
3. Select **Run workflow**.
4. Keep `run_telegram_live` disabled for the first run. This runs the automated Windows/Linux, security-regression, provider-regression, and Android build jobs.
5. To run the real Telegram preflight, first add these two repository Actions secrets:
   - `MATRIX_TELEGRAM_BOT_TOKEN`
   - `MATRIX_TELEGRAM_CHAT_ID`
6. Re-run the workflow with `run_telegram_live=true`. It sends exactly one harmless preflight message to the configured private chat.

Repository secret settings:
https://github.com/ayaanraza2008/matrix-ai/settings/secrets/actions

## What a green run does and does not prove

| Workflow job | What it verifies | What it does not certify |
|---|---|---|
| Core validation | Full test suite, Ruff, secret scan and CLI doctor on GitHub Ubuntu/Windows runners | Startup/restart/sleep/wake or voice on the owner's physical PC |
| Security regression | DNS-pinning and default-off regression tests | Independent human security approval |
| Provider regression | YouTube adapter configuration/policy tests with mocked services | Real provider OAuth consent, live authenticated read or write |
| Android build | Debug and release APK assembly; uploads APK artifacts | Physical handset pairing/reconnect, or production signing |
| Telegram live | Bot API preflight and one test message | Full command/task/restart/unauthorized-sender acceptance on the owner's live MATRIX runtime |

The report job posts the exact workflow job results to issue #73. Skipped or unavailable checks are reported as NOT RUN, never as PASS.

## Remaining owner/reviewer acceptance

- **Security:** a qualified reviewer who did not author the DNS-pinning code must independently inspect the implementation and record identity (or explicit anonymity), date, decision, and findings on issue #73. Keep `MATRIX_ALLOW_WEB_FETCH=false`.
- **Telegram:** after the one-message preflight succeeds, the complete long-polling bridge still needs live command, task delivery, restart/idempotency, unauthorized sender and emergency-stop tests.
- **Windows:** hosted CI is not the owner's target PC. Physical hardware/audio, sleep/wake, startup and restore acceptance remain separate.
- **Tailscale:** the owner's tailnet, restrictive ACLs, an approved test device, an unauthorized test identity and revocation are required. This first workflow does not create a tailnet device or expose MATRIX publicly.
- **Android:** physical handset acceptance and owner-controlled release signing/key custody remain separate.
- **OAuth:** provider consent and real authenticated read-only tests require owner-authorized provider credentials. Do not add secrets for providers that are not part of the intended deployment.

Never put secrets in commits, issue comments, logs or ChatGPT. Prefer repository/environment Actions secrets. Do not use public Funnel or port forwarding as a shortcut.
