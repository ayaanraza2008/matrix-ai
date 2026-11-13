# External capability add-ons

MATRIX has a pinned catalogue for the 19 repositories listed below. The catalogue is not the same as an installed or running service: repositories are cloned lazily under MATRIX_HOME/addons/<id>, at the reviewed commit shown in addon_catalog.py. They are never added as Git submodules or copied into the core MATRIX source tree.

Source install is deliberately **source-only**. It uses a sparse checkout, fetches one pinned commit, does not run npm install, pip install, build scripts or postinstall hooks, and does not grant the source direct tool authority. Packages/services must be installed and launched by the owner separately.

## Catalogue

| Add-on | Role | Licence | MATRIX integration mode |
|---|---|---|---|
| [OpenWhispr](https://github.com/OpenWhispr/openwhispr) | Local desktop voice, dictation, transcription and notes | MIT | Remote Streamable HTTP MCP; API key required |
| [YouTube Automation Agent](https://github.com/darkzOGx/youtube-automation-agent) | Video generation, channel analytics, operator, review and publishing | MIT | Fixed local HTTP adapter |
| [Craft Anything](https://github.com/storytold/craft-anything) | Inspect/recreate software and scaffold projects | MIT OR Apache-2.0 | Owner-configured MCP candidate |
| [Free for Dev](https://github.com/jixserver/free-for-dev) | Discover free developer-service tiers | No recognised repo licence | Read-only link catalogue |
| [HexStrike AI](https://github.com/0x4m4/hexstrike-ai) | Authorized defensive security assessment and pentesting tools | MIT | Owner-configured MCP bridge; all tools high risk by default |
| [Mobile MCP](https://github.com/mobile-next/mobile-mcp) | Android/iOS simulator and device automation | Apache-2.0 | Owner-configured local MCP |
| [Awesome Free LLM APIs](https://github.com/mnfst/awesome-free-llm-apis) | Discover free-tier model APIs and endpoints | CC0-1.0 | Read-only provider catalogue |
| [Harness²](https://github.com/google-research/harness2) | Agent harness/evaluation research | Apache-2.0 | Source/reference only; isolated evaluation required |
| [G0DM0D3](https://github.com/elder-plinius/G0DM0D3) | Prompt-injection and jailbreak red-team corpus | AGPL-3.0 | Adversarial tests only; never normal planner context |
| [Cal.diy](https://github.com/calcom/cal.diy) | Self-hosted scheduling service | MIT | Source/reference; very large (over 1 GB), personal/non-production guidance |
| [REA](https://github.com/morluto/rea) | Reverse engineering, application and binary inspection | MIT | Owner-configured local MCP |
| [ECC](https://github.com/affaan-m/ECC) | Engineering, TDD, review, harness and security skills | MIT | Selected reference skills only; no hook/script auto-install |
| [Build Your Own X](https://github.com/codecrafters-io/build-your-own-x) | Tutorials for implementing systems from scratch | No recognised repo licence | Read-only link catalogue |
| [OpenViking](https://github.com/volcengine/OpenViking) | Agent context, knowledge and memory filesystem | AGPL-3.0 | Optional external service; MATRIX SQLite remains primary |
| [Anthropic Cybersecurity Skills](https://github.com/mukul975/Anthropic-Cybersecurity-Skills) | Defensive security, detection, incident response and security reference | Apache-2.0 | Defensive references only; exploit-oriented content quarantined |
| [AgentMemory](https://github.com/rohitg00/agentmemory) | Persistent memory across agent sessions | Apache-2.0 | Owner-configured local MCP; optional second memory store |
| [Scientific Agent Skills](https://github.com/K-Dense-AI/scientific-agent-skills) | 177 scientific/research skills and databases | MIT | Selected bounded reference skills |
| [Browser Use](https://github.com/browser-use/browser-use) | Local browser agent and Chrome DevTools Protocol automation | MIT | Owner-configured local MCP |
| [AI Engineering Coach](https://github.com/microsoft/AI-Engineering-Coach) | Coding-session analytics, anti-pattern detection, skill discovery and context health | MIT | Pinned source-only engineering guidance; native dashboard requires VS Code |

Unknown/no-recognised licence repositories (Free for Dev and Build Your Own X) are treated as local link indices only and cannot be cloned by the installer. The links within those catalogues have their own licences and terms. AGPL-3.0 projects (OpenViking and G0DM0D3) are kept as separable optional sources; network deployments and redistribution must respect AGPL obligations.

## How MATRIX selects add-ons

For a free-form request, MATRIX matches the request against the add-on catalogue. It may retrieve bounded Markdown references only from repositories which are installed and marked as context-enabled. Retrieved material is labelled untrusted reference data; it cannot grant a tool, change scopes, approve an action or replace the policy engine.

MATRIX excludes G0DM0D3, HexStrike and Harness² from normal model context. The cybersecurity skill pack exposes only its repository overview, framework mappings and standards-reference files; operational exploit playbooks and skill execution instructions are never injected into normal planner context. Free-for-Dev and Build Your Own X are link catalogues, not executable agents. A context-enabled repository selected by a task is automatically sparse-fetched at its pinned commit when its estimated size is <=200 MB and MATRIX_ADDON_AUTO_INSTALL_SOURCES is true (default). Larger repositories are never auto-fetched.

## Owner API

All management routes require the owner bearer token:

- GET /api/addons — catalogue, pinned commit, install/connection state and risk metadata.
- GET /api/addons/route?q=... — preview of capability matches.
- POST /api/addons/{id}/install — sparse source install only.
- POST /api/addons/{id}/connect — connect one MCP server already configured locally.
- DELETE /api/addons/{id}/connect — disconnect and unregister its dynamic tools.
- GET /api/addons/tools — typed tools exposed by currently connected MCP servers.
- GET /api/tasks/{task_id}/agents — also includes the add-ons selected for that task and whether reference context was injected.

A repository larger than 200 MB requires an explicit second step:
POST /api/addons/cal-diy/install with {"allow_large": true}. The default install never downloads the 1.1 GB Cal.diy monorepo.

## Install and connect

To install sources for offline reference, call the owner route. Source installation does not execute package managers. For example:

~~~powershell
# Start MATRIX first; the API uses the owner bearer token.
$headers = @{ Authorization = "Bearer <owner-token>" }
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/addons/agentmemory/install -Headers $headers
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/addons/scientific-agent-skills/install -Headers $headers
~~~

To expose MCP tools, configure local owner-owned server commands in MATRIX_ADDON_MCP_SERVERS. MATRIX accepts only the 18 catalogued add-on IDs; command, arguments, environment, HTTP credentials and read-only tool lists are never accepted from model output. It is best to store keys in the user's uncommitted .env, not in repository files.

Example OpenWhispr remote MCP configuration (create a personal API key in OpenWhispr first):

~~~powershell
$env:MATRIX_ADDON_MCP_SERVERS = '{"openwhispr":{"transport":"streamable_http","url":"https://mcp.openwhispr.com/mcp","headers":{"Authorization":"Bearer <OpenWhispr-personal-api-key>"},"read_only_tools":[]}}'
~~~

The only remote MCP hostname currently accepted is mcp.openwhispr.com. Set MATRIX_ADDON_AUTO_INSTALL_SOURCES=false to disable task-triggered source fetches. Its desktop app still needs to be installed/launched and microphone access granted for local listening. The remote MCP API exposes notes, folders, transcriptions and usage available to the supplied key; it does not turn arbitrary spoken words into approval for destructive actions.

Example local Browser Use MCP configuration (Python environment and Chrome must be installed by the owner):

~~~powershell
$env:MATRIX_ADDON_MCP_SERVERS = '{"browser-use":{"command":"uvx","args":["--from","browser-use==1.0.2","browser-use","--cli-mcp"],"read_only_tools":[]}}'
~~~

Other stdio-capable servers (Mobile MCP, REA, AgentMemory, Craft Anything) use their upstream-documented command and arguments. When those commands are configured in MATRIX_ADDON_MCP_SERVERS, MATRIX connects them only when the task router selects that add-on. HexStrike must be connected manually; it is never auto-connected. Do not copy an arbitrary command from a third-party comment. For each connection, review the server command, target access and dependencies first.

After setting the environment, restart MATRIX and explicitly connect the desired server:

~~~powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/addons/browser-use/connect -Headers $headers
Invoke-RestMethod -Uri http://127.0.0.1:8765/api/addons/tools -Headers $headers
~~~

Each connected MCP tool is dynamically registered as addon.<id>.<tool-name>. MCP annotations are not trusted for permissions: a tool is treated as read-only only when its readOnlyHint is true **and** its exact name is included in the local owner's read_only_tools list. All other tools are HIGH risk and need exact-action approval. All MCP output is marked untrusted and can force fresh approval for subsequent side effects.

## YouTube Automation Agent

The YouTube repository runs as a separate local service (default http://127.0.0.1:3456). Configure its upstream YouTube OAuth credentials and at least one text provider in that service's own uncommitted .env. This may use free-tier providers, but API quota, daily limits, region eligibility and provider terms apply; not every stage is guaranteed free. Keep its fact-checking and rights-review gates enabled.

MATRIX's adapter is opt-in:

~~~powershell
$env:MATRIX_YOUTUBE_AUTOMATION_ENABLED = "true"
$env:MATRIX_YOUTUBE_AUTOMATION_URL = "http://127.0.0.1:3456"
$env:MATRIX_YOUTUBE_AUTOMATION_API_KEY = "<service-API_KEY-if-configured>"
~~~

Restart MATRIX after setting these values. It exposes:
- Read-only dashboard, readiness, channel analytics and generation-job status.
- HIGH-risk youtube.generate_video and youtube.resume_job (quota-consuming generation).
- CRITICAL youtube.start_operator, youtube.approve_content and youtube.publish_now.

Generation is not the same as publishing. Content approval/publish tools require exact MATRIX approval, and upstream may require fact-checked/rights-confirmed content. An operator run cannot silently publish content through MATRIX without the critical approval step.

The URL is restricted to loopback/private networks. Public internet service URLs are rejected. API keys never appear in /api/settings, task summaries or audit details.

## Free LLM providers

Awesome Free LLM APIs is a reference catalogue only. MATRIX does not harvest keys, auto-register random services, rotate through providers or send prompts to third parties. Set an OpenAI-compatible endpoint and key yourself after checking the provider's current data-retention/training policy, region and quotas. Cloud use remains opt-in via MATRIX configuration. Free-tier availability can change and may be limited.

## Safety and operational boundaries

- All real tool actions, including remote MCP actions, still go through the central typed registry, server-side scopes, policy and exact approval broker.
- Browser forms, purchases, uploads, account changes, mobile taps/installs and all security-tool operations default to HIGH risk unless a specific read-only tool is allowlisted in owner configuration.
- HexStrike is for authorized targets/labs only. Its external tools must be installed separately; configure and isolate its API server, then connect the MCP bridge. Set MATRIX_HEXSTRIKE_ALLOWED_TARGETS to exact hosts/IPs you own or have written permission to test. The adapter rejects target arguments outside that list, and HexStrike tools always remain HIGH risk. Do not expose it to public networks.
- Harness² documentation describes executors with sandbox disabled/unrestricted networking; MATRIX does not offer it as a normal MCP service. Run evaluations only in a disposable isolated environment.
- G0DM0D3 is a jailbreak/red-team corpus; it is not added to planner prompts or normal work contexts.
- OpenViking and AgentMemory remain separate optional memory services. MATRIX's SQLite memory remains the source of truth; no private memory is mirrored until an owner explicitly configures a second store.
- Cal.diy's own README recommends personal/non-production usage; the repository is over 1 GB and requires a database/runtime stack.
- Reverse engineering is limited to software/binaries you own or have explicit permission to inspect. Scientific/clinical outputs are research assistance, not patient-specific diagnoses or treatment recommendations.
- Disconnect an add-on with DELETE /api/addons/{id}/connect. MATRIX unregisters its tools and terminates the corresponding local process.


## AI Engineering Coach

[Microsoft AI Engineering Coach](https://github.com/microsoft/AI-Engineering-Coach) is available as a pinned, source-only engineering-coach add-on. MATRIX can select it automatically for coding-quality, prompt-quality, agentic-workflow, anti-pattern and skill-discovery tasks, install its reviewed source snapshot lazily, and retrieve bounded Markdown references. Retrieved content remains untrusted guidance and cannot grant tools, change policy, approve actions or rewrite MATRIX configuration.

**Important runtime boundary:** upstream currently ships as a VS Code extension and a GitHub Copilot app canvas, not as a headless service or documented standalone HTTP/MCP API. Its full session analytics and dashboard require VS Code 1.125+ and a locally built VSIX (or the supported Copilot canvas build). MATRIX therefore does **not** claim to run the upstream dashboard or invoke its internal analytics autonomously. This catalogue integration provides autonomous reference/skill guidance only; a future native bridge must be implemented and tested before claiming automated session ingestion or analytics inside MATRIX.

The upstream extension is read-only with respect to coding-session logs and reports local processing/no proprietary telemetry. MATRIX must preserve those boundaries: do not upload raw transcripts, do not scan unrelated directories, and do not install the VSIX or run npm lifecycle/build scripts as part of task routing. Only the pinned source snapshot is fetched by MATRIX. To use the native dashboard separately, follow the upstream build instructions in its README and install the generated VSIX yourself.


## Native MATRIX engineering coach

MATRIX also ships a native, local analyzer exposed to the planner as
`engineering_coach.analyze_session`. The tool is read-only and requires the
existing `files.read` permission. It analyzes only an explicitly selected
`.jsonl` or `.ndjson` session export inside one of MATRIX's configured
filesystem roots. The default root is MATRIX_HOME/workspace; additional roots
must be explicitly configured by the owner.

Example task: "Analyze workspace/session.jsonl and summarize recurring tool
failures and reusable workflows."

The analyzer has a 10 MiB and 50,000-line limit. It returns aggregate tool
counts, failure rates, repeated failure patterns, and repeated successful
two-step workflows as skill candidates. It never returns or persists prompt
text, tool arguments, or raw records and performs no network requests.

Supported inputs:
- MATRIX-normalized JSONL: tool events use `type: "tool_call"` or
  `type: "tool_result"` and a `tool_name` field.
- Claude Code transcript JSONL: assistant `tool_use` blocks and user
  `tool_result` blocks are reduced to tool names and error flags.
- Codex CLI rollout JSONL: `response_item` function-call records are reduced
  to tool names and error flags. Function-call output records are counted as
  failures only when the export provides an explicit error flag/status.

GitHub Copilot / VS Code session exports do not yet have a supported stable
adapter in MATRIX; convert them to the normalized schema before analysis.
Format adapters discard prompt text, tool arguments and tool output at the
parsing boundary. The dashboard includes an owner-authenticated Engineering
Coach view at **Engineering Coach**; enter one file path and select Analyze.
The API endpoint is `POST /api/engineering-coach/analyze`.

There is deliberately no directory watcher, log discovery, or
silent ingestion. Analysis is on-demand against the one explicit file path
inside an approved filesystem root. The dashboard also offers an owner-opt-in
recurring job for that same exact file (hourly, every six hours, or daily);
the path is validated before the job is created, each run still uses the
read-only `files.read` tool and current filesystem-root policy, and the job
can be paused through the dashboard or `PATCH /api/jobs/{job_id}`. This is
periodic re-analysis of one known file, not automatic discovery or ingestion
of new files.

Repeated workflows are suggestions only: MATRIX does not automatically create
or execute new skills. An owner can review a candidate and separately author
a skill through the normal governed workflow. This avoids turning noisy or
malicious session data into executable instructions.


## AI Engineering Coach

[Microsoft AI Engineering Coach](https://github.com/microsoft/AI-Engineering-Coach) is available as a pinned, source-only engineering-coach add-on. MATRIX can select it automatically for coding-quality, prompt-quality, agentic-workflow, anti-pattern and skill-discovery tasks, install its reviewed source snapshot lazily, and retrieve bounded Markdown references. Retrieved content remains untrusted guidance and cannot grant tools, change policy, approve actions or rewrite MATRIX configuration.

**Important runtime boundary:** upstream currently ships as a VS Code extension and a GitHub Copilot app canvas, not as a headless service or documented standalone HTTP/MCP API. Its full session analytics and dashboard require VS Code 1.125+ and a locally built VSIX (or the supported Copilot canvas build). MATRIX therefore does **not** claim to run the upstream dashboard or invoke its internal analytics autonomously. This catalogue integration provides autonomous reference/skill guidance only; a future native bridge must be implemented and tested before claiming automated session ingestion or analytics inside MATRIX.

The upstream extension is read-only with respect to coding-session logs and reports local processing/no proprietary telemetry. MATRIX must preserve those boundaries: do not upload raw transcripts, do not scan unrelated directories, and do not install the VSIX or run npm lifecycle/build scripts as part of task routing. Only the pinned source snapshot is fetched by MATRIX. To use the native dashboard separately, follow the upstream build instructions in its README and install the generated VSIX yourself.


## Native MATRIX engineering coach

MATRIX also ships a native, local analyzer exposed to the planner as
`engineering_coach.analyze_session`. The tool is read-only and requires the
existing `files.read` permission. It analyzes only an explicitly selected
`.jsonl` or `.ndjson` session export inside one of MATRIX's configured
filesystem roots. The default root is MATRIX_HOME/workspace; additional roots
must be explicitly configured by the owner.

Example task: "Analyze workspace/session.jsonl and summarize recurring tool
failures and reusable workflows."

The analyzer has a 10 MiB and 50,000-line limit. It returns aggregate tool
counts, failure rates, repeated failure patterns, and repeated successful
two-step workflows as skill candidates. It never returns or persists prompt
text, tool arguments, or raw records and performs no network requests.

Supported inputs:
- MATRIX-normalized JSONL: tool events use `type: "tool_call"` or
  `type: "tool_result"` and a `tool_name` field.
- Claude Code transcript JSONL: assistant `tool_use` blocks and user
  `tool_result` blocks are reduced to tool names and error flags.
- Codex CLI rollout JSONL: `response_item` function-call records are reduced
  to tool names and error flags. Function-call output records are counted as
  failures only when the export provides an explicit error flag/status.

GitHub Copilot / VS Code session exports do not yet have a supported stable
adapter in MATRIX; convert them to the normalized schema before analysis.
Format adapters discard prompt text, tool arguments and tool output at the
parsing boundary. The dashboard includes an owner-authenticated Engineering
Coach view at **Engineering Coach**; enter one file path and select Analyze.
The API endpoint is `POST /api/engineering-coach/analyze`.

There is deliberately no directory watcher, log discovery, or
silent ingestion. Analysis is on-demand against the one explicit file path
inside an approved filesystem root. The dashboard also offers an owner-opt-in
recurring job for that same exact file (hourly, every six hours, or daily);
the path is validated before the job is created, each run still uses the
read-only `files.read` tool and current filesystem-root policy, and the job
can be paused through the dashboard or `PATCH /api/jobs/{job_id}`. This is
periodic re-analysis of one known file, not automatic discovery or ingestion
of new files.

Repeated workflows are suggestions only: MATRIX does not automatically create
or execute new skills. An owner can review a candidate and separately author
a skill through the normal governed workflow. This avoids turning noisy or
malicious session data into executable instructions.


## AI Engineering Coach

[Microsoft AI Engineering Coach](https://github.com/microsoft/AI-Engineering-Coach) is available as a pinned, source-only engineering-coach add-on. MATRIX can select it automatically for coding-quality, prompt-quality, agentic-workflow, anti-pattern and skill-discovery tasks, install its reviewed source snapshot lazily, and retrieve bounded Markdown references. Retrieved content remains untrusted guidance and cannot grant tools, change policy, approve actions or rewrite MATRIX configuration.

**Important runtime boundary:** upstream currently ships as a VS Code extension and a GitHub Copilot app canvas, not as a headless service or documented standalone HTTP/MCP API. Its full session analytics and dashboard require VS Code 1.125+ and a locally built VSIX (or the supported Copilot canvas build). MATRIX therefore does **not** claim to run the upstream dashboard or invoke its internal analytics autonomously. This catalogue integration provides autonomous reference/skill guidance only; a future native bridge must be implemented and tested before claiming automated session ingestion or analytics inside MATRIX.

The upstream extension is read-only with respect to coding-session logs and reports local processing/no proprietary telemetry. MATRIX must preserve those boundaries: do not upload raw transcripts, do not scan unrelated directories, and do not install the VSIX or run npm lifecycle/build scripts as part of task routing. Only the pinned source snapshot is fetched by MATRIX. To use the native dashboard separately, follow the upstream build instructions in its README and install the generated VSIX yourself.


## Native MATRIX engineering coach

MATRIX also ships a native, local analyzer exposed to the planner as
`engineering_coach.analyze_session`. The tool is read-only and requires the
existing `files.read` permission. It analyzes only an explicitly selected
`.jsonl` or `.ndjson` session export inside one of MATRIX's configured
filesystem roots. The default root is MATRIX_HOME/workspace; additional roots
must be explicitly configured by the owner.

Example task: "Analyze workspace/session.jsonl and summarize recurring tool
failures and reusable workflows."

The analyzer has a 10 MiB and 50,000-event limit. It returns aggregate tool
counts, failure rates, repeated failure patterns, and repeated successful
two-step workflows as skill candidates. It never returns or persists prompt
text, tool arguments, or raw records and performs no network requests. It does
not watch directories, silently ingest sessions, or assume that VS Code,
Copilot, Claude Code, Codex, or any other agent stores logs in a common format.
Exports must be normalized JSONL with one event object per line; recognizable
tool events use `type: "tool_call"` or `type: "tool_result"` and a
`tool_name` field. MATRIX only analyzes the file path explicitly supplied
for that task.

