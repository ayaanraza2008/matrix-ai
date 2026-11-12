# Free-first app integration readiness

This document separates **working integration code**, **connectivity checks**, **source-only add-ons**, and **unimplemented app actions**. A credential check is not an app integration: MATRIX must expose a typed action, execute it through the central policy engine, verify the provider response, and record a redacted audit event before an action can be called working.

## Product target

MATRIX is the single orchestrator. Telegram is a remote chat client, not a second agent. Ordinary services are connected as bounded capabilities; autonomous third-party agent applications are optional and excluded from the default integration set.

The default deployment should remain local-first and free of paid AI APIs. A provider may still impose its own quotas, OAuth consent requirements, account eligibility, or hosting costs. MATRIX must disclose these before enabling a capability and must never claim a provider is free merely because its client library is open source.

## Current capability truth (repository snapshot)

| Service / route | What exists | What is still needed before calling it fully integrated |
|---|---|---|
| Telegram Bot API | Optional health/credential probe on main; private two-way polling bridge is proposed in PR #19 | Merge its dependency chain, configure a fresh bot token and owner chat ID, run the Windows process, and pass a real private-chat send/receive/stop test |
| GitHub | Token validation adapter on the base branch; native MATRIX read-only account/repository/issue/PR tools are proposed in PR #25 | Merge the connector chain, configure a fine-grained read-only token, add approved write actions, and pass live account/repository acceptance tests |
| Google Workspace | OAuth client-file validation only | OAuth consent/callback/token refresh plus scoped Gmail and Calendar actions and provider-side verification |
| Home Assistant | API reachability/token probe only | Entity discovery, read-state and service-call tools, area/device allowlists, confirmation for state-changing actions |
| Open-Meteo | No-key connectivity probe | Typed forecast tool with validated location/timezone inputs, caching, rate limits and response tests |
| Firebase Cloud Messaging | Service-account file validation only | Device registration, token lifecycle, authenticated send tool and Android end-to-end test |
| Browser Use / Mobile MCP / OpenWhispr / Craft Anything / AgentMemory and other catalogue entries | Optional source catalogue and owner-configured MCP bridge for selected entries | Install each upstream runtime separately, configure its owner-controlled server, review exact tool names, and test on the target device |
| Twilio telephony | Credential probe; carrier usage is paid | Excluded from the free baseline; never enable without explicit cost disclosure |

The external add-on catalogue is not proof that every upstream repository is installed, running, or connected. Source installation is deliberately separate from executable service installation.

## Free baseline

The no-paid-service baseline should prioritize:

1. Telegram Bot API long polling for remote text chat (no public webhook or inbound port).
2. Ollama or another local OpenAI-compatible model for optional local reasoning; deterministic tools remain available without a model.
3. Open-Meteo for weather without an API key.\n4. Native GitHub read-only actions using a fine-grained token with only the repository/account read permissions needed.
4. Home Assistant for local smart-home control where the user already runs Home Assistant.
5. Local browser automation only after a reviewed local runtime is installed.
6. Local SQLite memory and local workspace storage.
7. Tailscale private networking as an optional free personal-tier route, subject to current provider terms and availability.

Do not make Twilio/PSTN, paid hosted LLMs, paid voice APIs, paid cloud hosting, or paid connector tiers prerequisites for the baseline.

## Integration implementation contract

Every ordinary app connector must provide:

- A stable ID, display name, category, capability list, required settings, and an explicit cost/hosting label.
- A status endpoint that distinguishes `missing_credentials`, `configured`, `working`, `rate_limited`, `permission_denied`, `degraded`, and `unavailable`.
- Typed request and response schemas with strict size, timeout, and pagination limits.
- Least-privilege OAuth scopes or API tokens; credentials remain in environment variables or an OS secret store and are never returned by settings/status endpoints.
- Read-only operations as the default. Writes, sends, uploads, deletes, purchases, account changes, and device controls require exact-action approval unless a narrowly reviewed low-risk policy says otherwise.
- Server-side authorization, rate limiting, idempotency where supported, bounded retries, and independent provider-response verification.
- Redacted audit records containing operation IDs and outcome metadata, never message bodies, file contents, tokens, or full provider payloads.
- Mocked contract tests plus a documented live smoke test that the owner can run on Windows.
- A disconnect/revoke path that unregisters tools and removes cached tokens without deleting user-owned provider data.

MCP servers are treated as untrusted capability providers. The owner must configure each server locally. Tool annotations are not sufficient to grant read-only status; exact tool names must be reviewed and allowlisted. All unreviewed tools remain high risk and require approval.

## Telegram acceptance gate

Telegram is ready for daily use only after all of the following pass on the user's Windows PC:

- Bot token is stored in a local environment variable and was never committed or pasted into source.
- The configured numeric owner chat ID and sender ID are both checked on every update.
- Private text messages reach the same MATRIX orchestrator as dashboard chat.
- `/help`, `/status`, and `/stop` work; `/stop` remains available during rate limiting.
- High/critical actions cannot be approved in Telegram; the dashboard remains the approval surface.
- Pending approval tasks deliver their final outcome to Telegram after the owner decides in the dashboard.
- Duplicate/replayed updates do not execute a task twice.
- Polling survives a temporary network interruption and shuts down cleanly.
- No public webhook, port-forward, or Tailscale Funnel is required.
- Real Telegram delivery and a Windows restart/sign-in test are recorded as environment acceptance, not inferred from CI.

## Release gates for "working"

Do not advertise a connector as working based only on source presence, successful import, credential-file existence, or a mocked unit test. A release must publish separate counts for:

- implemented actions;
- unit/contract tests passed;
- live provider checks passed;
- owner setup steps still required;
- capabilities blocked by provider policy, account tier, quotas, or paid services.

A provider outage or exhausted free quota must degrade that connector without crashing MATRIX or disabling unrelated local capabilities.
