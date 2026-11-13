# Telegram control bridge

MATRIX uses Telegram Bot API long polling. It does not open an inbound port or configure a public webhook.

## Setup

1. Create a bot with @BotFather and copy its token directly into the MATRIX process environment.
2. Obtain the numeric ID of your private Telegram chat.
3. Set `MATRIX_TELEGRAM_BOT_TOKEN` and `MATRIX_TELEGRAM_CHAT_ID` in the local `.env` or service environment.
4. Restart MATRIX and send `/start` to the bot from that private chat.

Do not paste the bot token into chat, source control, issue comments or screenshots. If a token has ever been exposed, revoke it with BotFather and create a replacement.

## Commands

- Plain text creates a normal MATRIX task through the policy-checked orchestrator.
- A Telegram voice note is downloaded in memory, limited to 8 MB, transcribed by the configured local Whisper engine and submitted as a normal task.
- `/status` reports local model health, active task count, pending owner approvals and emergency-stop state.
- `/tasks` lists the five most recent tasks submitted through this Telegram chat.
- `/task <task-id>` reports the state and result of one Telegram-submitted task.
- `/cancel <task-id>` cancels one Telegram-submitted task; it cannot target dashboard or system tasks.
- `/help` lists the supported commands.
- `/stop` activates the emergency stop and cancels active tasks.
- `/resume` clears the emergency stop for new tasks. It does not restart tasks cancelled by `/stop`.

The bridge accepts only a private chat whose chat ID and sender ID both match the configured owner ID. It shares the runtime rate limiter, audits failures without storing message content, and cannot approve HIGH/CRITICAL actions. Approvals must be confirmed in the authenticated MATRIX dashboard. Telegram task lookup and cancellation are limited to tasks whose durable idempotency record was created by this private Telegram bridge.

### Restart and delivery behavior

The bridge persists the last successfully handled Telegram update ID in the local database and resumes polling from the next ID after restart. It does not deliberately discard pending owner messages at startup. If handling an update fails, the bridge does not advance the watermark, so Telegram can deliver that update again. Task creation is idempotent by Telegram update ID: a replay reuses the existing task rather than creating a duplicate. Delivery is **at-least-once**, not exactly-once; a crash around an external side effect can still require operator review. See [TELEGRAM_RELIABILITY.md](TELEGRAM_RELIABILITY.md).

## Voice and calling limits

Voice-note transcription requires the optional local `matrix-ai[voice]` dependencies and a working faster-whisper installation/model. MATRIX does not currently place or answer PSTN/WhatsApp phone calls through this bridge. Telephony requires a separately configured provider and may incur charges; no free calling guarantee is made.

## Privacy

Telegram is a third-party cloud service. Telegram messages and voice notes are sent to Telegram, and voice-note bytes are temporarily held in memory for local transcription. MATRIX does not persist the raw audio. The transcript is treated as untrusted user input and still passes through MATRIX policy and approval checks.
