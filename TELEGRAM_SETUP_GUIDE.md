# Telegram Setup Guide for MATRIX Live Acceptance

## Prerequisites

- Owner must have a Telegram account
- Owner must have access to BotFather (the official Telegram bot API helper)
- Owner must know their numeric chat ID with the bot

## Step 1: Create a Telegram Bot

1. Open Telegram and find @BotFather
2. Send the command `/newbot`
3. BotFather will ask for a bot name (anything, e.g. "MATRIX_Test_Bot")
4. BotFather will ask for a unique bot username (endswith `bot`, e.g. "matrix_test_bot")
5. BotFather will respond with the bot token — **save this value**

**Critical:** The bot token is sensitive. Never share it in public channels, issues, or chat.

## Step 2: Get Your Chat ID

1. Start a conversation with your new bot in Telegram
2. Send any message to the bot
3. Use one of the following methods to find your chat ID:

### Method A: Using @userinfobot
1. Message @userinfobot in Telegram
2. It will reply with your user ID and chat ID

### Method B: Using the Telegram API (manual)
1. Construct: `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
2. Send a message to your bot from your account
3. Look at the JSON response — find `"chat":{"id":<NUMBER>,"type":"private"}`
4. **Save this numeric chat ID**

**Important:** The chat ID must be a private (1:1) chat, not a group or channel.

## Step 3: Configure Environment Variables

Set the following environment variables in the MATRIX execution environment:

```powershell
# Set the bot token (kept secret)
$env:MATRIX_TELEGRAM_BOT_TOKEN="<YOUR_BOT_TOKEN>"

# Set the numeric chat ID (public to the bot, but sensitive in context)
$env:MATRIX_TELEGRAM_CHAT_ID="<YOUR_CHAT_ID>"
```

**Alternative: Add to a local .env file** (never commit to source control):
```
MATRIX_TELEGRAM_BOT_TOKEN=<YOUR_BOT_TOKEN>
MATRIX_TELEGRAM_CHAT_ID=<YOUR_CHAT_ID>
```

## Step 4: Verify the Configuration

Run the preflight command to verify the bot credentials work:

```powershell
matrix telegram-preflight
```

Expected output (JSON):
```json
{
  "token_configured": true,
  "chat_id_configured": true,
  "bot_username": "your_bot_username",
  "bot_id": 123456789,
  "bot_can_read_all_group_messages": false,
  "webhook_url": "",
  "pending_updates": 0
}
```

## Step 5: Send a Test Message

Run with the `--send-test` flag to send a harmless test message to the configured chat:

```powershell
matrix telegram-preflight --send-test
```

Expected: A message "MATRIX telegram-preflight test message." arrives in the configured private chat.

## Security Notes

- **Never paste the bot token or chat ID into this conversation, GitHub issues, or public forums.**
- The bot can only send messages to the configured chat ID — it cannot broadcast to other chats.
- The bot can receive messages only from the configured chat ID (owner-only enforcement).
- Use a dedicated bot for MATRIX, not your primary bot.
- Rotate/revoke the bot token if compromised via @BotFather's `/revoke` command.

## Troubleshooting

| Issue | Resolution |
|---|---|
| `matrix telegram-preflight` fails with "bot token rejected" | Verify the token was copied correctly from BotFather's response; ensure no extra whitespace |
| "chat_id not configured" | Verify `MATRIX_TELEGRAM_CHAT_ID` is set to the numeric ID, not a username |
| "Webhook is configured" | The bot has a webhook set up; delete it via `/deleteWebhook` in BotFather, or use the webhook integration instead |
| Test message not received | Verify the chat ID is from a private (1:1) chat, not a group or channel; ensure the bot can message you |