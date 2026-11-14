/**
 * MATRIX AI — Telegram Webhook Relay Worker (Serverless & VPS-Free)
 *
 * Cloudflare Worker edge function (Free Tier: 100,000 requests/day, $0 cost).
 *
 * Architecture:
 * 1. Telegram sends an HTTPS POST webhook when you send a message to @matrixai_v1_bot.
 * 2. This Worker verifies the secret token header (X-Telegram-Bot-Api-Secret-Token).
 * 3. It checks that the sender matches your authorized Telegram Owner ID.
 * 4. It triggers GitHub repository dispatch ('event_type': 'telegram-webhook') on your repo.
 * 5. GitHub Actions immediately runs `.github/workflows/cloud-scheduler.yml` (<30s latency).
 *
 * Environment Variables / Secrets required in Cloudflare Worker:
 * - TELEGRAM_SECRET_TOKEN: Secret string configured with setWebhook.
 * - TELEGRAM_OWNER_ID: Your numeric Telegram ID (e.g., 123456789).
 * - GITHUB_REPO: "ayaanraza2008/matrix-ai"
 * - GITHUB_TOKEN: GitHub fine-grained PAT with "Contents: write" or "Actions: write" to dispatch workflows.
 *
 * Setup:
 * 1. Create a free Worker on cloudflare.com.
 * 2. Paste this file into the Worker editor.
 * 3. Add the 4 environment secrets in Cloudflare Worker settings.
 * 4. Register the webhook with Telegram:
 *    curl -F "url=https://<your-worker>.<subdomain>.workers.dev" \
 *         -F "secret_token=<TELEGRAM_SECRET_TOKEN>" \
 *         "https://api.telegram.org/bot<MATRIX_TELEGRAM_BOT_TOKEN>/setWebhook"
 */

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("Method Not Allowed. MATRIX Webhook accepts POST only.", { status: 405 });
    }

    // 1. Verify Secret Token Header
    const secretToken = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (!env.TELEGRAM_SECRET_TOKEN || secretToken !== env.TELEGRAM_SECRET_TOKEN) {
      return new Response("Unauthorized: Invalid secret token.", { status: 401 });
    }

    let payload;
    try {
      payload = await request.json();
    } catch (err) {
      return new Response("Bad Request: Invalid JSON.", { status: 400 });
    }

    // 2. Validate Message & Sender Authentication
    const message = payload.message || payload.edited_message;
    if (!message) {
      // Non-message update (e.g. channel post or reaction)
      return new Response("OK (Ignored non-message update)", { status: 200 });
    }

    const senderId = String(message.from?.id || "");
    const allowedId = String(env.TELEGRAM_OWNER_ID || "");

    if (allowedId && senderId !== allowedId) {
      // Reject unauthorized user attempts silently or return 403
      console.warn(`Unauthorized message attempt from Telegram ID: ${senderId}`);
      return new Response("Forbidden: Unauthorized sender.", { status: 403 });
    }

    // 3. Trigger GitHub repository_dispatch for near real-time execution
    const githubRepo = env.GITHUB_REPO || "ayaanraza2008/matrix-ai";
    const githubToken = env.GITHUB_TOKEN;

    if (githubToken) {
      const dispatchUrl = `https://api.github.com/repos/${githubRepo}/dispatches`;
      ctx.waitUntil(
        fetch(dispatchUrl, {
          method: "POST",
          headers: {
            "Accept": "application/vnd.github+json",
            "Authorization": `Bearer ${githubToken}`,
            "User-Agent": "matrix-ai-cloudflare-relay",
            "X-GitHub-Api-Version": "2022-11-28",
          },
          body: JSON.stringify({
            event_type: "telegram-webhook",
            client_payload: {
              update_id: payload.update_id,
              message_id: message.message_id,
              text: (message.text || "").slice(0, 100),
            },
          }),
        }).catch((err) => console.error("GitHub dispatch error:", err))
      );
    }

    // 4. Return 200 OK immediately to Telegram
    return new Response(JSON.stringify({ status: "queued", update_id: payload.update_id }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  },
};
