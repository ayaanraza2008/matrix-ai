"""Logical agent roles.

MATRIX runs one model runtime; these roles are prompt/tool-permission profiles,
not separate heavyweight processes. Keeping them declarative makes the
responsibility boundaries (and what each role may request) reviewable.
"""

from __future__ import annotations

AGENT_ROLES: tuple[dict, ...] = (
    {
        "name": "orchestrator",
        "responsibility": "Owns the task lifecycle, state transitions, retries, cancellation and reporting.",
        "tools": ["planner", "policy", "tool registry"],
        "may_request_scopes": [],
    },
    {
        "name": "planner",
        "responsibility": "Decompose a goal into ordered steps with dependencies.",
        "tools": ["read-only project context"],
        "may_request_scopes": ["memory.read", "files.read"],
    },
    {
        "name": "researcher",
        "responsibility": "Gather and compare public information with citations and uncertainty.",
        "tools": ["web.fetch_public", "memory.search"],
        "may_request_scopes": ["web.read", "memory.read"],
    },
    {
        "name": "coder",
        "responsibility": "Inspect and modify repositories inside an authorised workspace.",
        "tools": ["files.read_text", "files.write_text", "shell.run_allowed"],
        "may_request_scopes": ["files.read", "files.write", "shell.allowlisted"],
    },
    {
        "name": "vision",
        "responsibility": "Describe images and screenshots; OCR within documented tolerance.",
        "tools": ["vision.ocr_image", "vision session"],
        "may_request_scopes": ["vision.read", "files.read"],
    },
    {
        "name": "voice",
        "responsibility": "Control microphone state and speech synthesis sessions.",
        "tools": ["voice session", "voice.transcribe", "voice.speak"],
        "may_request_scopes": [],
    },
    {
        "name": "desktop",
        "responsibility": "Perform scoped Windows actions: applications, files, telemetry.",
        "tools": ["apps.open", "apps.close", "files.*", "system.get_status"],
        "may_request_scopes": ["apps.launch", "apps.control", "files.read", "files.write", "system.read"],
    },
    {
        "name": "memory",
        "responsibility": "Maintain governed memory with provenance, retention and deletion.",
        "tools": ["memory.search", "memory.save"],
        "may_request_scopes": ["memory.read", "memory.write"],
    },
    {
        "name": "security",
        "responsibility": "Evaluate risk and policy; never grants itself authority.",
        "tools": ["policy engine", "audit log (read-only)"],
        "may_request_scopes": [],
    },
    {
        "name": "monitoring",
        "responsibility": "Check health, model state and integration status.",
        "tools": ["system.get_status", "health checks"],
        "may_request_scopes": ["system.read"],
    },
    {
        "name": "scheduler",
        "responsibility": "Run owner-configured recurring jobs within stored limits.",
        "tools": ["scheduler API"],
        "may_request_scopes": [],
    },
    {
        "name": "verifier",
        "responsibility": "Validate claimed outcomes with independent read-only evidence.",
        "tools": ["tool verify()", "files.stat", "system.get_status"],
        "may_request_scopes": ["files.read", "system.read"],
    },
)


def agent_catalog() -> list[dict]:
    return [
        {**role, "status": "logical-role", "separate_process": False}
        for role in AGENT_ROLES
    ]
