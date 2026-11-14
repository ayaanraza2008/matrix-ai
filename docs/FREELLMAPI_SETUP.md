# FreeLLMAPI setup for MATRIX

MATRIX uses FreeLLMAPI through its OpenAI-compatible HTTP interface. FreeLLMAPI documents a local router base URL of `http://localhost:3001/v1`, a unified API key, and the `/v1/chat/completions` and `/v1/models` endpoints.

## Configure the local router

1. Install and start FreeLLMAPI on the same trusted machine as MATRIX.
2. Create or copy a unified key from the FreeLLMAPI dashboard.
3. Keep the router bound to loopback unless you have a separately reviewed private-network deployment.

## Configure MATRIX

Set these environment variables in the MATRIX process environment:

- `MATRIX_MODEL_PROVIDER=freellmapi`
- `MATRIX_FREELLMAPI_URL=http://127.0.0.1:3001/v1`
- `MATRIX_FREELLMAPI_API_KEY=<your-unified-key>`
- `MATRIX_ALLOW_CLOUD_MODELS=true`

The API key is required and must stay in the environment or an OS-backed secret store. Never commit it, place it in a URL, or include it in issue reports.

## Consent and data boundary

The router itself is local, but it forwards inference requests to whichever upstream providers are configured in FreeLLMAPI. MATRIX therefore blocks this provider unless `MATRIX_ALLOW_CLOUD_MODELS=true` is explicitly set. Only set that flag after reviewing which upstream providers are enabled and what data they receive.

## Health checks

MATRIX probes `GET /v1/models` with the same Bearer key used for completions. A configured router is not considered healthy unless the authenticated endpoint responds successfully. Missing credentials are reported as `unconfigured`, not as a working provider.
