"""Built-in MATRIX roles and the upstream Agency Agents specialist catalog.

Specialist Markdown is reference material only. Tool authority remains with
MATRIX's typed registry, scope grants, policy engine and approval broker.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
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
    "a an and are as at be can create do for from get give help how i in into is it "
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
    return {
        token.lower()
        for token in _TOKEN.findall(value or "")
        if token.lower() not in _STOPWORDS
    }


# Lightweight domain expansion keeps routing useful without embeddings or a cloud API.
# These aliases are recall hints, not permissions or claims about tool availability.
_AGENT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "website": ("web", "frontend", "ui", "design", "react", "accessibility"),
    "build": ("software", "engineering", "code", "development", "backend", "frontend", "architecture"),
    "platform": ("engineering", "backend", "infrastructure", "architecture", "devops"),
    "launch": ("marketing", "growth", "campaign", "product", "go-to-market"),
    "web": ("website", "frontend", "ui", "react", "vue", "angular"),
    "site": ("website", "web", "frontend", "ui", "design"),
    "dashboard": ("frontend", "ui", "ux", "visualization", "data", "design"),
    "ui": ("frontend", "ux", "design", "accessibility"),
    "ux": ("ui", "design", "research", "accessibility"),
    "frontend": ("web", "ui", "ux", "react", "vue", "css", "accessibility"),
    "react": ("frontend", "web", "javascript", "typescript", "ui"),
    "backend": ("api", "server", "database", "architecture", "python"),
    "api": ("backend", "server", "integration", "architecture"),
    "database": ("backend", "sql", "data", "architecture"),
    "deploy": ("devops", "infrastructure", "cloud", "ci", "cd", "operations"),
    "deployment": ("devops", "infrastructure", "cloud", "ci", "cd", "operations"),
    "docker": ("devops", "containers", "infrastructure", "deployment"),
    "production": ("devops", "reliability", "security", "testing", "operations"),
    "bug": ("debugging", "testing", "qa", "quality", "engineering"),
    "test": ("testing", "qa", "quality", "automation", "validation"),
    "secure": ("security", "privacy", "threat", "vulnerability", "compliance", "backend", "authentication"),
    "security": ("privacy", "threat", "vulnerability", "pentest", "compliance"),
    "audit": ("security", "compliance", "testing", "quality", "risk"),
    "research": ("analysis", "academic", "sources", "evidence", "strategy"),
    "analyze": ("analysis", "research", "data", "analytics"),
    "analytics": ("data", "metrics", "visualization", "research"),
    "data": ("analytics", "database", "statistics", "pipeline"),
    "marketing": ("brand", "seo", "social", "content", "growth", "campaign"),
    "youtube": ("video", "content", "social", "marketing", "audience"),
    "video": ("editing", "production", "motion", "content", "marketing"),
    "sales": ("crm", "revenue", "pipeline", "outreach", "negotiation"),
    "product": ("roadmap", "requirements", "discovery", "strategy", "ux"),
    "android": ("mobile", "kotlin", "compose", "application"),
    "mobile": ("android", "ios", "kotlin", "application", "frontend"),
    "game": ("gaming", "unity", "unreal", "game-development"),
    "finance": ("accounting", "budget", "forecast", "financial", "risk"),
    "support": ("customer", "helpdesk", "service", "documentation"),
    "automation": ("workflow", "orchestration", "agent", "integration", "devops"),
    "agent": ("automation", "orchestration", "workflow", "ai", "multi-agent"),
}

_TEAM_CUES = (
    "end to end",
    "end-to-end",
    "comprehensive",
    "full project",
    "full system",
    "complete system",
    "production ready",
    "production-ready",
    "audit and improve",
    "research and implement",
    "design and build",
    "plan and execute",
    "multiple specialists",
    "multi agent",
    "multi-agent",
    "team of agents",
)


@lru_cache(maxsize=4)
def _agent_search_index(root_text: str) -> tuple[tuple[dict[str, Any], ...], dict[str, float]]:
    """Build a cached BM25-style index over metadata and full profile instructions."""
    agents = _load_cached(root_text)
    documents: list[dict[str, Any]] = []
    document_frequency: Counter[str] = Counter()

    for agent in agents:
        name_terms = _tokens(f"{agent['name']} {agent['slug']}")
        description_terms = _tokens(agent["description"])
        division_terms = _tokens(agent["division"].replace("-", " "))
        vibe_terms = _tokens(agent.get("vibe", ""))
        body_terms = Counter(_TOKEN.findall(agent.get("content", "").lower()))
        terms = name_terms | description_terms | division_terms | vibe_terms | set(body_terms)
        document_frequency.update(terms)
        documents.append(
            {
                "agent": agent,
                "name_terms": name_terms,
                "description_terms": description_terms,
                "division_terms": division_terms,
                "vibe_terms": vibe_terms,
                "body_terms": body_terms,
            }
        )

    count = max(1, len(documents))
    inverse_document_frequency = {
        term: math.log(1.0 + (count - frequency + 0.5) / (frequency + 0.5))
        for term, frequency in document_frequency.items()
    }
    return tuple(documents), inverse_document_frequency


def _expand_query_terms(query: str) -> set[str]:
    original = _tokens(query)
    expanded = set(original)
    for term in original:
        expanded.update(_AGENT_SYNONYMS.get(term, ()))
    return expanded


def _rank_agency_agents(
    query: str, *, root: str | Path | None = None
) -> list[dict[str, Any]]:
    directory = Path(root).expanduser().resolve() if root is not None else agency_agents_dir()
    documents, idf = _agent_search_index(str(directory))
    query_terms = _expand_query_terms(query)
    if not query_terms:
        return []

    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for document in documents:
        score = 0.0
        matched_terms: set[str] = set()
        matched_fields: set[str] = set()
        name_terms = document["name_terms"]
        description_terms = document["description_terms"]
        division_terms = document["division_terms"]
        vibe_terms = document["vibe_terms"]
        body_terms = document["body_terms"]

        for term in query_terms:
            weight = idf.get(term, 0.0)
            if weight <= 0:
                continue
            if term in name_terms:
                score += weight * 5.0
                matched_fields.add("name")
                matched_terms.add(term)
            if term in description_terms:
                score += weight * 2.5
                matched_fields.add("description")
                matched_terms.add(term)
            if term in division_terms:
                score += weight * 2.0
                matched_fields.add("division")
                matched_terms.add(term)
            if term in vibe_terms:
                score += weight * 1.2
                matched_fields.add("vibe")
                matched_terms.add(term)
            frequency = body_terms.get(term, 0)
            if frequency:
                score += weight * 0.28 * min(frequency, 4)
                matched_fields.add("instructions")
                matched_terms.add(term)

        if not matched_terms:
            continue

        # Normalize for long user messages so short, focused queries stay competitive.
        score /= math.sqrt(max(1, len(query_terms)))
        agent = document["agent"]
        ranked.append(
            (
                score,
                agent["name"].casefold(),
                {
                    **agent,
                    "relevance_score": round(score, 4),
                    "matched_terms": sorted(matched_terms)[:16],
                    "matched_fields": sorted(matched_fields),
                },
            )
        )

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [agent for _, _, agent in ranked]


def select_agency_agents(
    query: str, *, limit: int = 2, root: str | Path | None = None
) -> list[dict[str, Any]]:
    """Rank the entire pinned corpus by specialist relevance; returns full profiles."""
    try:
        bounded_limit = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        bounded_limit = 2
    return _rank_agency_agents(query, root=root)[:bounded_limit]


def _automatic_team_size(query: str, candidates: list[dict[str, Any]]) -> int:
    lowered = " ".join((query or "").lower().split())
    word_count = len(_TOKEN.findall(query or ""))
    divisions = {
        agent["division"]
        for agent in candidates[:6]
        if agent["relevance_score"] >= candidates[0]["relevance_score"] * 0.55
    } if candidates else set()

    if any(cue in lowered for cue in _TEAM_CUES) or word_count >= 36:
        return 4
    if word_count >= 20 or (word_count >= 11 and len(divisions) >= 2):
        return 3
    if word_count >= 7 or len(divisions) >= 2:
        return 2
    return 1


def route_agency_agents(
    query: str, *, max_agents: int | None = None, root: str | Path | None = None
) -> list[dict[str, Any]]:
    """Autonomously choose a bounded lead/support team from all available profiles."""
    ranked = _rank_agency_agents(query, root=root)
    if not ranked or ranked[0]["relevance_score"] < 0.08:
        return []

    target_size = _automatic_team_size(query, ranked) if max_agents is None else max(
        1, min(int(max_agents), 4)
    )
    best_score = ranked[0]["relevance_score"]
    # Broad tasks need division coverage. Keep the lead first, then prefer the
    # strongest relevant profile from a new division before adding duplicates.
    ratio = 0.18 if target_size >= 3 else 0.38
    eligible = [
        candidate for candidate in ranked
        if candidate["relevance_score"] >= max(0.08, best_score * ratio)
    ]
    ordered: list[dict[str, Any]] = eligible[:1]
    used_divisions = {eligible[0]["division"]}
    for candidate in eligible[1:]:
        if len(ordered) >= target_size:
            break
        if candidate["division"] not in used_divisions:
            ordered.append(candidate)
            used_divisions.add(candidate["division"])
    if len(ordered) < target_size:
        for candidate in eligible[1:]:
            if len(ordered) >= target_size:
                break
            if candidate not in ordered:
                ordered.append(candidate)

    selected: list[dict[str, Any]] = []
    for candidate in ordered:
        item = dict(candidate)
        item["routing_role"] = "lead" if not selected else "support"
        terms = item.get("matched_terms", [])
        item["selection_reason"] = (
            "Best overall match across specialist name, description and instructions."
            if not selected
            else "Complementary domain match for: " + ", ".join(terms[:6])
        )
        selected.append(item)
    return selected


def search_agency_agents(
    query: str, *, limit: int = 5, root: str | Path | None = None
) -> list[dict[str, Any]]:
    """Search the catalog and omit long prompt bodies from list responses."""
    results = select_agency_agents(query, limit=limit, root=root)
    return [{key: value for key, value in agent.items() if key != "content"} for agent in results]


def render_specialist_context(
    agents: list[dict[str, Any]], *, max_agent_chars: int = 1800, max_total_chars: int = 9000
) -> str:
    """Serialize a bounded lead/support team as untrusted domain guidance."""
    payload: list[dict[str, Any]] = []
    remaining = max(0, min(int(max_total_chars), 12_000))
    per_agent = max(1, min(int(max_agent_chars), 3_000))
    for agent in agents[:4]:
        if remaining <= 0:
            break
        item = {
            "slug": agent.get("slug", ""),
            "name": agent["name"],
            "division": agent["division"],
            "routing_role": agent.get("routing_role", "support"),
            "selection_reason": agent.get("selection_reason", ""),
            "description": agent["description"],
            "reference_instructions": str(agent.get("content", ""))[:per_agent],
        }
        encoded = json.dumps(item, ensure_ascii=False)
        if len(encoded) > remaining:
            available = max(0, remaining - 240)
            item["reference_instructions"] = item["reference_instructions"][:available]
            encoded = json.dumps(item, ensure_ascii=False)
        if len(encoded) <= remaining:
            payload.append(item)
            remaining -= len(encoded)
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
