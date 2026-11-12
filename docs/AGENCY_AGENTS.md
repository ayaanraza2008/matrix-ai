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
- Free-form requests are matched against agent names, descriptions, division,
  and vibe. Up to two relevant profiles are selected for planner context.
- The chosen Markdown instructions are truncated and serialized into a small
  reference payload. The model still runs through MATRIX's existing local model
  manager; this does not spawn 283 workers or require another paid API.
- Deterministic heuristic tasks continue to use their existing typed plans.
  Specialist routing is used only for free-form/model-planned requests.

Authenticated API endpoints:

- GET /api/agents — built-in roles plus loaded specialist metadata.
- GET /api/agents/search?q=react&limit=5 — ranked specialist metadata.
- GET /api/agents/{slug} — full specialist profile, including its instructions.

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

This integration is a specialist prompt-routing layer over one local model
runtime, not a claim that every upstream profile is an independently running
agent. True parallel multi-model execution would increase memory and compute
usage and is intentionally not enabled by this change.
