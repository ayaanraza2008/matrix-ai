# Vercel Agent Skills integration

MATRIX integrates the official Vercel Skills CLI as an optional, autonomous capability-discovery layer. The CLI package is pinned to skills@1.8.1; its upstream runtime requirement is Node.js 22.20.0 or newer.

## Runtime behaviour

For free-form tasks when a local model is configured, MATRIX:

1. Searches the public skills.sh search API using the task text.
2. Validates returned skill names and GitHub owner/repository identifiers.
3. Installs at most one top search result per task when it is not already installed.
4. Installs into MATRIX_HOME/skills/cli-project/.agents/skills using the pinned Vercel CLI and copy mode.
5. Routes up to three installed skills by name/description relevance.
6. Adds only each selected SKILL.md to the planner as explicitly untrusted reference context.

The integration never runs skill-provided scripts, hooks, package managers, shell commands, MCP servers, or code. A skill cannot add MATRIX tools, scopes, permissions, or approvals. The planner may only propose registered MATRIX tools; the existing central policy, exact-action approval, emergency stop and verification pipeline remain authoritative. Existing deterministic intents do not incur a remote skills search. If discovery or installation fails, planning continues without a skill.

## Configuration

| Environment variable | Default | Purpose |
|---|---:|---|
| MATRIX_SKILLS_AUTO_DISCOVER | true | Search skills.sh for free-form tasks |
| MATRIX_SKILLS_AUTO_INSTALL | true | Install at most one relevant result per task |
| MATRIX_SKILLS_AUTO_UPDATE | false | Update installed skills at most once per 24 hours when enabled |

Set the corresponding variable to false to disable discovery or automatic installation. Set MATRIX_SKILLS_AUTO_UPDATE=true to opt into daily updates of installed skills. The skill store is under MATRIX_HOME; it is not committed into the MATRIX source repository. The first use may download the pinned CLI through npx; no npm dependency is added to MATRIX's Python runtime.

## Owner operations

The Python SkillsManager exposes status(), search(query), list_installed(), install(source, skill_name), and update() for owner interfaces and future dashboard controls. The current task planner performs discovery, bounded installation and routing automatically. Node.js 22.20.0+ and npx must be installed on the MATRIX host for CLI operations. Without them, existing skills can still be loaded, but new skills cannot be installed.

## Security and limitations

- The skills.sh search request contains the task text (truncated to 120 characters); do not enable network discovery for sensitive workflows.
- Only public GitHub owner/repository sources and conservative skill names returned by the search API are accepted for automatic installs.
- CLI subprocess calls use argument arrays (shell=False), a timeout, a pinned package version and a reduced environment that excludes MATRIX tokens.
- Installed directories and SKILL.md paths are checked against the managed root; symlinked skill directories/files are ignored.
- Skill instructions are untrusted prompt data. They are not code execution, not verified truth, and never a source of authority.
- The official CLI requires Node.js >=22.20.0; MATRIX remains a Python 3.11+ application and can run without this optional integration.
