"""Built-in MATRIX roles and the upstream Agency Agents specialist catalog.

Specialist Markdown is reference material only. Tool authority remains with
MATRIX's typed registry, scope grants, policy engine and approval broker.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

UPSTREAM_REPOSITORY = "msitarzewski/agency-agents"
UPSTREAM_COMMIT = "8ae6164602c95ca78828b148b9052460ee426885"

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

_DIVISIONS = (
    "academic",
    "design",
    "engineering",
    "finance",
    "game-development",
    "gis",
    "healthcare",
    "marketing",
    "paid-media",
    "product",
    "project-management",
    "research",
    "sales",
    "security",
    "spatial-computing",
    "specialized",
    "support",
    "testing",
)
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.-]{1,}", re.I)
_STOPWORDS = frozenset(
    "a an and are as at be build can create do for from get give help how i in into is it "
    "me my of on or our please project should tell that the their them then this to use want "
    "we what when with you your".split()
)


def agency_agents_dir() -> Path:
    configured = os.getenv("MATRIX_AGENCY_AGENTS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "agency-agents"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    match = _FRONTMATTER.match(text)
    if match is None:
        return None
    metadata: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        metadata[key.strip()] = value
    if not metadata.get("name") or not metadata.get("description"):
        return None
    return metadata, text[match.end():].strip()


@lru_cache(maxsize=4)
def _load_cached(root_text: str) -> tuple[dict[str, Any], ...]:
    root = Path(root_text)
    if not root.is_dir():
        return ()
    agents: list[dict[str, Any]] = []
    for division in _DIVISIONS:
        division_dir = root / division
        if not division_dir.is_dir():
            continue
        for path in sorted(division_dir.glob("*.md")):
            try:
                parsed = _parse_frontmatter(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                continue
            if parsed is None:
                continue
            metadata, body = parsed
            agents.append(
                {
                    "id": path.stem,
                    "slug": path.stem,
                    "name": metadata["name"],
                    "description": metadata["description"],
                    "division": division,
                    "emoji": metadata.get("emoji", ""),
                    "vibe": metadata.get("vibe", ""),
                    "color": metadata.get("color", ""),
                    "content": body,
                    "source_repository": UPSTREAM_REPOSITORY,
                    "source_revision": UPSTREAM_COMMIT,
                    "authority": "prompt_only",
                    "separate_process": False,
                }
            )
    return tuple(agents)


def load_agency_agents(root: str | Path | None = None) -> list[dict[str, Any]]:
    """Load valid agent definitions from the pinned upstream submodule."""
    directory = Path(root).expanduser().resolve() if root is not None else agency_agents_dir()
    return [dict(agent) for agent in _load_cached(str(directory))]


def get_agency_agent(slug: str, root: str | Path | None = None) -> dict[str, Any] | None:
    if not isinstance(slug, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", slug) is None:
        return None
    return next((agent for agent in load_agency_agents(root) if agent["slug"] == slug), None)


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in _TOKEN.findall(value or "") if token.lower() not in _STOPWORDS}


def select_agency_agents(
    query: str, *, limit: int = 2, root: str | Path | None = None
) -> list[dict[str, Any]]:
    """Rank relevant profiles lexically; returned bodies are reference data, not authority."""
    query_terms = _tokens(query)
    if not query_terms:
        return []
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for agent in load_agency_agents(root):
        name_terms = _tokens(agent["name"] + " " + agent["slug"])
        description_terms = _tokens(agent["description"])
        vibe_terms = _tokens(agent.get("vibe", ""))
        division_terms = _tokens(agent["division"].replace("-", " "))
        score = (
            4 * len(query_terms & name_terms)
            + 2 * len(query_terms & description_terms)
            + len(query_terms & vibe_terms)
            + len(query_terms & division_terms)
        )
        if score:
            ranked.append((score, agent["name"].casefold(), agent))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    bounded_limit = max(1, min(int(limit), 10))
    return [{**agent, "relevance_score": score} for score, _, agent in ranked[:bounded_limit]]


def search_agency_agents(
    query: str, *, limit: int = 5, root: str | Path | None = None
) -> list[dict[str, Any]]:
    """Search the catalog and omit long prompt bodies from list responses."""
    results = select_agency_agents(query, limit=limit, root=root)
    return [{key: value for key, value in agent.items() if key != "content"} for agent in results]


def render_specialist_context(agents: list[dict[str, Any]], *, max_agent_chars: int = 2500) -> str:
    """Serialize only a small, bounded set of specialist references for the planner."""
    payload = [
        {
            "name": agent["name"],
            "division": agent["division"],
            "description": agent["description"],
            "reference_instructions": str(agent.get("content", ""))[:max_agent_chars],
        }
        for agent in agents[:2]
    ]
    return json.dumps(payload, ensure_ascii=False)


def agent_catalog(root: str | Path | None = None) -> list[dict[str, Any]]:
    """Expose built-in logical roles plus available upstream specialist profiles."""
    built_in = [
        {
            **role,
            "id": role["name"],
            "slug": role["name"],
            "kind": "builtin",
            "status": "logical-role",
            "separate_process": False,
        }
        for role in AGENT_ROLES
    ]
    specialists = [
        {key: value for key, value in agent.items() if key != "content"}
        | {"kind": "specialist", "status": "available"}
        for agent in load_agency_agents(root)
    ]
    return built_in + specialists
