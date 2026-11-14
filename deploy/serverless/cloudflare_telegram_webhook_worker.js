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
 * 5. It checks if this update was already successfully dispatched (via optional KV or in-memory set).
 * 6. It triggers GitHub repository dispatch ('event_type': 'telegram-webhook') on your repo,
 *    passing the full update in client_payload.
 * 7. It awaits the GitHub response (expecting HTTP 204). If GitHub dispatch fails, it returns
 *    HTTP 502 to Telegram WITHOUT recording the update in the dispatched cache, ensuring
 *    Telegram's automated webhook retries are NEVER suppressed.
 * 8. ONLY after GitHub returns HTTP 204 is the update marked as successfully dispatched.
 * 9. Never logs bot tokens, webhook secrets, or private message content.
 *
 * Environment Variables / Secrets required in Cloudflare Worker:
 * - TELEGRAM_SECRET_TOKEN: Secret string configured with setWebhook (MANDATORY).
 * - TELEGRAM_OWNER_ID: Your numeric Telegram ID, e.g. 123456789 (MANDATORY).
 * - GITHUB_REPO: "ayaanraza2008/matrix-ai" (Default: "ayaanraza2008/matrix-ai")
 * - GITHUB_TOKEN: GitHub fine-grained PAT with "Contents: write" or "Actions: write" (MANDATORY).
 *
 * Optional Durable Bindings:
 * - MATRIX_WEBHOOK_KV: Cloudflare KV namespace binding for durable edge deduplication across isolates.
 *   If not bound, the worker automatically falls back to ephemeral in-memory set deduplication.
 *   Note: The authoritative source of truth for durable idempotency is MATRIX AI's SQLite database.
 */

const RECENT_DISPATCHED_LIMIT = 1000;
const recentDispatchedUpdateIds = new Set();

/**
 * Check if update has already been successfully dispatched to GitHub.
 * Reads from optional durable KV store first, then in-memory set.
 */
async function isAlreadyDispatched(env, updateId) {
  if (env && env.MATRIX_WEBHOOK_KV && typeof env.MATRIX_WEBHOOK_KV.get === "function") {
    try {
      const val = await env.MATRIX_WEBHOOK_KV.get(`dispatched:${updateId}`);
      if (val !== null) {
        return true;
      }
    } catch {
      // Non-fatal KV lookup error; fall back to in-memory cache
    }
  }
  return recentDispatchedUpdateIds.has(updateId);
}

/**
 * Mark update as dispatched ONLY after GitHub repository_dispatch confirms HTTP 204.
 */
async function markAsDispatched(env, updateId) {
  recentDispatchedUpdateIds.add(updateId);
  if (recentDispatchedUpdateIds.size > RECENT_DISPATCHED_LIMIT) {
    const oldest = recentDispatchedUpdateIds.values().next().value;
    recentDispatchedUpdateIds.delete(oldest);
  }

  if (env && env.MATRIX_WEBHOOK_KV && typeof env.MATRIX_WEBHOOK_KV.put === "function") {
    try {
      // 24 hour TTL for durable webhook deduplication
      await env.MATRIX_WEBHOOK_KV.put(`dispatched:${updateId}`, "1", { expirationTtl: 86400 });
    } catch {
      // Non-fatal KV write error
    }
  }
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
    const secretTokenConfig = env && env.TELEGRAM_SECRET_TOKEN ? String(env.TELEGRAM_SECRET_TOKEN).trim() : "";
    const ownerIdConfig = env && env.TELEGRAM_OWNER_ID ? String(env.TELEGRAM_OWNER_ID).trim() : "";
    const githubToken = env && env.GITHUB_TOKEN ? String(env.GITHUB_TOKEN).trim() : "";
    const githubRepo = env && env.GITHUB_REPO ? String(env.GITHUB_REPO).trim() : "ayaanraza2008/matrix-ai";

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

    // 4. Duplicate Check (Do NOT mark as dispatched yet!)
    // If an earlier attempt was already successfully dispatched to GitHub, return 200 immediately
    // so Telegram does not keep sending redundant webhook retries.
    if (await isAlreadyDispatched(env, payload.update_id)) {
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
        // Dispatch failed! CRITICAL: Do NOT mark as dispatched!
        // Return 502 Bad Gateway to Telegram so Telegram retries delivery.
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

      // 7. Mark as dispatched ONLY AFTER confirmed HTTP 204 from GitHub
      await markAsDispatched(env, payload.update_id);
    } catch {
      // Network or fetch error! CRITICAL: Do NOT mark as dispatched!
      // Return 502 to Telegram so Telegram retries delivery.
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

    // 8. Return 200 OK to Telegram
    return new Response(
      JSON.stringify({ status: "dispatched", update_id: payload.update_id }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }
    );
  },
};
