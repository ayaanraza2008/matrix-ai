# Agency Agents specialist integration

MATRIX vendors the complete upstream repository as a Git submodule at
https://github.com/msitarzewski/agency-agents, pinned to commit
8ae6164602c95ca78828b148b9052460ee426885. That snapshot contains 283 agent
Markdown profiles across 18 divisions. The upstream MIT license and original
agent files remain in the submodule; this project does not rewrite or relicense
those files.

## Install and update

Fresh clone (recommended):

~~~powershell
git clone --recurse-submodules https://github.com/ayaanraza2008/matrix-ai.git
cd matrix-ai
~~~

For an existing checkout:

~~~powershell
git submodule update --init --recursive
~~~

If the submodule was not initialized, MATRIX continues to work with its 12
built-in logical roles; specialist routing is simply unavailable until the
submodule is populated.

To review a future upstream update:

~~~powershell
git -C agency-agents fetch origin
git -C agency-agents checkout <reviewed-commit>
git add agency-agents
git commit -m "chore(agents): update Agency Agents snapshot"
~~~

Review the upstream diff and license before changing the pinned commit.

## Runtime behavior

- The existing 12 MATRIX logical roles remain intact.
- The API catalog adds the upstream specialist profiles with division, name,
  description, emoji/vibe metadata, source commit, and prompt-only authority.
- Every free-form planning request is automatically searched against all
  loaded profile metadata and the full Markdown instruction body with a cached,
  weighted BM25-style lexical index. Domain aliases improve matching for common
  tasks such as frontend/backend work, APIs, security, deployment, research,
  analytics, marketing, mobile, testing, and product delivery.
- MATRIX automatically chooses a lead specialist and, for broader or
  multi-domain tasks, up to three supporting specialists. Selection is bounded
  to four profiles and to a fixed prompt-context budget; it does not load all
  283 profiles into every model call.
- Each profile is instantiated as its own worker object with a stable ID,
  independent run lock, queue accounting, status, run counters and last-run telemetry.
- MATRIX sends selected lead/support workers the task independently. They produce
  bounded JSON analysis reports, scheduled concurrently up to the configured worker cap.
  A single coordinator combines those untrusted reports with profile reference material
  and proposes the normal MATRIX plan.
- Worker reports may suggest tool names but cannot execute tools or supply trusted
  arguments. The central orchestrator validates every step, evaluates policy, obtains
  exact approval when required, executes registered tools and verifies evidence.
- The task audit records the routing strategy, selected specialist IDs,
  divisions, match terms, scores, and whether the guidance was actually injected.
- Deterministic, exact-intent operations continue to use their typed plans
  directly, because they do not need a second model decision. Their behavior is
  not delayed or widened by specialist routing.
- MATRIX uses one configured local model runtime; these profiles are not 283
  separately running model processes. No additional paid API is required.

Authenticated API endpoints:

- GET /api/agents — built-in roles plus loaded specialist metadata.
- GET /api/agents/search?q=react&limit=5 — ranked specialist metadata.
- GET /api/agents/route?q=... — automatic lead/support team preview for a request.
- GET /api/agents/{slug} — full specialist profile, including its instructions.
- GET /api/tasks/{task_id}/agents — selected specialists, individual worker reports and whether context reached the planner.
- GET /api/workers — worker fleet status and all loaded worker snapshots (no prompt bodies).
- GET /api/workers/{slug} — one worker's current status, counters and last-run metadata.

## Autonomy and safety boundaries

Agency profiles are expertise references, not executable plugins and not
permission grants. They cannot add tools, widen a task's scopes, set risk,
approve actions, or override MATRIX system rules. The planner can only propose
tools already present in the typed registry. MATRIX validates arguments and
re-evaluates policy immediately before execution.

The existing L0-L4 autonomy policy, exact-action approvals, path allowlists,
untrusted-content tainting, deadlines, cancellation, emergency stop, and
verification remain authoritative. HIGH and CRITICAL actions continue to require
the existing exact approval path. A specialist recommendation never counts as
verification or proof of completion.

The fleet contains 283 independent in-process agent instances with separate
identity, run locks, queue metrics and result state. They are not 283 separately
loaded foundation models or OS processes: inference is multiplexed through one
configured ModelManager and bounded by MATRIX_MODEL_MAX_CONCURRENT_REQUESTS
(default 1). Specialist actors can be scheduled concurrently, while a low-resource
local inference server may serialize their actual model calls. Task-level reports
are persisted in the SQLite audit trail; live per-worker counters reset on restart.

## Worker runtime configuration

- MATRIX_AGENT_MAX_CONCURRENT_WORKERS (default 4, max 16) bounds active specialist actors.
- MATRIX_AGENT_WORKER_TIMEOUT_SECONDS (default 90, max 300) bounds each worker run.
- MATRIX_MODEL_MAX_CONCURRENT_REQUESTS (default 1, max 8) independently bounds calls to the shared model provider.

The upstream prompt corpus contains illustrative secret-like strings and private-key
markers as security-training examples. The MATRIX first-party secret scanner
therefore excludes only the `agency-agents/` vendored directory; all MATRIX source,
configuration, docs, and other repository paths remain covered. The upstream files
are preserved unchanged, with their original MIT license.
