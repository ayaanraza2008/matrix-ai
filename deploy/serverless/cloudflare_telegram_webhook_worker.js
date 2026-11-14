/**
 * MATRIX AI — Telegram Webhook Relay Worker (Serverless & VPS-Free)
 *
 * Cloudflare Worker edge function (Free Tier: 100,000 requests/day, $0 cost).
 *
 * Architecture:
 * 1. Telegram sends an HTTPS POST webhook when you send a message to @matrixai_v1_bot.
 * 2. This Worker validates mandatory configuration (fails closed if any secret is unset).
 * 3. It verifies the secret token header (X-Telegram-Bot-Api-Secret-Token).
 * 4. It enforces strict owner authentication: private chat, matching sender ID and chat ID.
 * 5. It deduplicates replays using an in-memory update_id cache.
 * 6. It triggers GitHub repository dispatch ('event_type': 'telegram-webhook') on your repo,
 *    passing the full update in client_payload.
 * 7. It awaits the GitHub response (expecting HTTP 204) and returns HTTP 502 to Telegram if
 *    dispatch fails, ensuring Telegram retries delivery if GitHub is temporarily unreachable.
 * 8. Never logs bot tokens, webhook secrets, or private message content.
 *
 * Environment Variables / Secrets required in Cloudflare Worker:
 * - TELEGRAM_SECRET_TOKEN: Secret string configured with setWebhook (MANDATORY).
 * - TELEGRAM_OWNER_ID: Your numeric Telegram ID, e.g. 123456789 (MANDATORY).
 * - GITHUB_REPO: "ayaanraza2008/matrix-ai" (Default: "ayaanraza2008/matrix-ai")
 * - GITHUB_TOKEN: GitHub fine-grained PAT with "Contents: write" or "Actions: write" (MANDATORY).
 */

const RECENT_UPDATE_LIMIT = 1000;
const recentUpdateIds = new Set();

function isDuplicate(updateId) {
  if (recentUpdateIds.has(updateId)) {
    return true;
  }
  recentUpdateIds.add(updateId);
  if (recentUpdateIds.size > RECENT_UPDATE_LIMIT) {
    const oldest = recentUpdateIds.values().next().value;
    recentUpdateIds.delete(oldest);
  }
  return false;
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("Method Not Allowed. MATRIX Webhook accepts POST only.", {
        status: 405,
        headers: { "Content-Type": "text/plain" },
      });
    }

    // 1. Mandatory Configuration Validation (Fail-Closed)
    // Never allow missing configuration to bypass authentication or fail silently.
    const secretTokenConfig = env.TELEGRAM_SECRET_TOKEN ? String(env.TELEGRAM_SECRET_TOKEN).trim() : "";
    const ownerIdConfig = env.TELEGRAM_OWNER_ID ? String(env.TELEGRAM_OWNER_ID).trim() : "";
    const githubToken = env.GITHUB_TOKEN ? String(env.GITHUB_TOKEN).trim() : "";
    const githubRepo = env.GITHUB_REPO ? String(env.GITHUB_REPO).trim() : "ayaanraza2008/matrix-ai";

    if (!secretTokenConfig || !ownerIdConfig || !githubToken) {
      // Configuration is incomplete - refuse requests securely without leaking details
      return new Response(
        JSON.stringify({
          error: "Server configuration incomplete. Ensure TELEGRAM_SECRET_TOKEN, TELEGRAM_OWNER_ID, and GITHUB_TOKEN are configured.",
        }),
        {
          status: 500,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    // 2. Verify Secret Token Header
    const secretHeader = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
    if (!secretHeader || secretHeader !== secretTokenConfig) {
      return new Response(JSON.stringify({ error: "Unauthorized: Invalid or missing secret token header." }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    }

    // 3. Parse and Validate Update Structure
    let payload;
    try {
      payload = await request.json();
    } catch {
      return new Response(JSON.stringify({ error: "Bad Request: Invalid JSON payload." }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      });
    }

    if (!payload || typeof payload !== "object" || typeof payload.update_id !== "number" || !Number.isInteger(payload.update_id)) {
      return new Response(JSON.stringify({ error: "Bad Request: Missing or invalid integer update_id." }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      });
    }

    // 4. Replay / Duplicate Protection
    if (isDuplicate(payload.update_id)) {
      return new Response(
        JSON.stringify({ status: "already_processed", update_id: payload.update_id }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    // 5. Validate Message & Sender Authorization
    const message = payload.message || payload.edited_message;
    if (!message) {
      // Non-message update (e.g. inline query, channel post, or reaction)
      return new Response(
        JSON.stringify({ status: "ignored_non_message", update_id: payload.update_id }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    const senderId = String(message.from?.id || "");
    const chatId = String(message.chat?.id || "");
    const chatType = message.chat?.type;

    // Strict authentication: must be private chat, sender ID must match owner, chat ID must match owner
    if (chatType !== "private" || senderId !== ownerIdConfig || chatId !== ownerIdConfig) {
      return new Response(JSON.stringify({ error: "Forbidden: Unauthorized sender or non-private chat." }), {
        status: 403,
        headers: { "Content-Type": "application/json" },
      });
    }

    // 6. Trigger GitHub repository_dispatch for near real-time execution
    const dispatchUrl = `https://api.github.com/repos/${githubRepo}/dispatches`;
    try {
      const dispatchResp = await fetch(dispatchUrl, {
        method: "POST",
        headers: {
          "Accept": "application/vnd.github+json",
          "Authorization": `Bearer ${githubToken}`,
          "User-Agent": "matrix-ai-cloudflare-relay",
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          event_type: "telegram-webhook",
          client_payload: {
            update_id: payload.update_id,
            update: payload,
          },
        }),
      });

      // GitHub repository_dispatch returns HTTP 204 No Content on success
      if (dispatchResp.status !== 204) {
        return new Response(
          JSON.stringify({
            error: "GitHub dispatch failed",
            github_status: dispatchResp.status,
            update_id: payload.update_id,
          }),
          {
            status: 502,
            headers: { "Content-Type": "application/json" },
          }
        );
      }
    } catch {
      return new Response(
        JSON.stringify({
          error: "GitHub dispatch network error",
          update_id: payload.update_id,
        }),
        {
          status: 502,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    // 7. Success: Return 200 OK to Telegram
    return new Response(
      JSON.stringify({ status: "dispatched", update_id: payload.update_id }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }
    );
  },
};
