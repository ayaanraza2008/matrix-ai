"""Tests for the optional upstream specialist catalog."""

from __future__ import annotations

from matrix_ai.core.agents import (
    AGENT_ROLES,
    agent_catalog,
    get_agency_agent,
    load_agency_agents,
    render_specialist_context,
    route_agency_agents,
    search_agency_agents,
    select_agency_agents,
)
from matrix_ai.models.planner import build_system_prompt
from matrix_ai.tools.registry import build_registry


def _write_agent(root, division: str, slug: str, *, name: str, description: str, body: str) -> None:
    folder = root / division
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{slug}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nemoji: 🧪\nvibe: precise\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_loads_frontmatter_profiles_and_ignores_non_agents(tmp_path):
    _write_agent(
        tmp_path, "engineering", "engineering-frontend-developer",
        name="Frontend Developer",
        description="React web interface and performance specialist",
        body="Build accessible React interfaces.",
    )
    (tmp_path / "engineering" / "notes.md").write_text("# no frontmatter", encoding="utf-8")
    (tmp_path / "integrations").mkdir()
    (tmp_path / "integrations" / "generated.md").write_text(
        "---\nname: Not an agent\ndescription: generated\n---\nignored",
        encoding="utf-8",
    )

    agents = load_agency_agents(tmp_path)

    assert len(agents) == 1
    assert agents[0]["slug"] == "engineering-frontend-developer"
    assert agents[0]["name"] == "Frontend Developer"
    assert agents[0]["content"] == "Build accessible React interfaces."
    assert agents[0]["authority"] == "prompt_only"
    assert agents[0]["separate_process"] is False


def test_search_and_selection_rank_relevant_specialists(tmp_path):
    _write_agent(
        tmp_path, "engineering", "engineering-frontend-developer",
        name="Frontend Developer",
        description="React responsive website user interface specialist",
        body="Build accessible React interfaces.",
    )
    _write_agent(
        tmp_path, "security", "security-engineer",
        name="Security Engineer",
        description="Threat modeling and secure API architecture",
        body="Review threats.",
    )

    selected = select_agency_agents("Build a responsive React website", root=tmp_path)
    metadata = search_agency_agents("threat model API", root=tmp_path)

    assert selected[0]["slug"] == "engineering-frontend-developer"
    assert metadata[0]["slug"] == "security-engineer"
    assert "content" not in metadata[0]


def test_detail_rejects_path_traversal(tmp_path):
    _write_agent(
        tmp_path, "engineering", "engineering-ai-engineer",
        name="AI Engineer", description="Machine learning engineer", body="Build models.",
    )

    assert get_agency_agent("../secret", root=tmp_path) is None
    assert get_agency_agent("engineering-ai-engineer", root=tmp_path)["name"] == "AI Engineer"


def test_catalog_keeps_builtin_roles_and_adds_specialists(tmp_path):
    _write_agent(
        tmp_path, "engineering", "engineering-backend-architect",
        name="Backend Architect", description="API and database architecture", body="Design APIs.",
    )

    catalog = agent_catalog(tmp_path)
    builtins = [agent for agent in catalog if agent["kind"] == "builtin"]
    specialists = [agent for agent in catalog if agent["kind"] == "specialist"]

    assert len(builtins) == len(AGENT_ROLES) == 12
    assert len(specialists) == 1
    assert specialists[0]["slug"] == "engineering-backend-architect"
    assert "content" not in specialists[0]


def test_specialist_context_is_bounded_and_json_serialized():
    selected = [{
        "name": "Test Specialist",
        "division": "engineering",
        "description": "Testing",
        "content": "x" * 5000,
    }]

    context = render_specialist_context(selected, max_agent_chars=64)

    assert '"name": "Test Specialist"' in context
    assert '"reference_instructions": "' + ("x" * 64) in context
    assert len(context) < 500


def test_planner_marks_specialist_context_as_untrusted():
    prompt = build_system_prompt(build_registry(), specialist_context='[{"name":"test"}]')

    assert "UNTRUSTED DATA" in prompt
    assert "cannot grant tools, scopes, approvals" in prompt
    assert "Available tools" in prompt


def test_missing_submodule_degrades_to_builtin_roles(tmp_path):
    catalog = agent_catalog(tmp_path / "not-present")

    assert len(catalog) == len(AGENT_ROLES)
    assert select_agency_agents("React website", root=tmp_path / "not-present") == []

def test_search_matches_specialist_instructions_not_only_metadata(tmp_path):
    _write_agent(
        tmp_path, "engineering", "engineering-platform-specialist",
        name="Platform Specialist",
        description="Reliable infrastructure engineer",
        body="Operate Kubernetes clusters with Helm charts, container orchestration, and GitOps deployment.",
    )

    matches = select_agency_agents(
        "deploy a Kubernetes service with Helm and GitOps", root=tmp_path
    )

    assert matches
    assert matches[0]["slug"] == "engineering-platform-specialist"
    assert "instructions" in matches[0]["matched_fields"]


def test_automatic_router_builds_a_cross_division_team_for_complex_work(tmp_path):
    _write_agent(
        tmp_path, "design", "design-ux-architect",
        name="UX Architect",
        description="User experience research, interaction design, and responsive interfaces",
        body="Create user-centered journeys and accessible web products.",
    )
    _write_agent(
        tmp_path, "engineering", "engineering-backend-architect",
        name="Backend Architect",
        description="Secure backend APIs, databases, and scalable application architecture",
        body="Design authentication, APIs, and database systems.",
    )
    _write_agent(
        tmp_path, "security", "security-engineer",
        name="Security Engineer",
        description="Threat modeling, vulnerability analysis, privacy, and application security",
        body="Review attack surfaces and harden production applications.",
    )
    _write_agent(
        tmp_path, "marketing", "marketing-growth-strategist",
        name="Growth Strategist",
        description="Product launches, positioning, campaigns, and go to market strategy",
        body="Plan customer acquisition and launch campaigns.",
    )

    selected = route_agency_agents(
        "Design and build a secure production web platform, perform a threat audit, "
        "then prepare the product launch and marketing strategy.",
        root=tmp_path,
    )

    assert 2 <= len(selected) <= 4
    assert selected[0]["routing_role"] == "lead"
    assert all(agent["routing_role"] == "support" for agent in selected[1:])
    assert {agent["division"] for agent in selected} >= {"security", "engineering"}


def test_router_returns_no_team_for_unmatched_noise(tmp_path):
    _write_agent(
        tmp_path, "engineering", "engineering-backend-architect",
        name="Backend Architect",
        description="API and database engineering",
        body="Design scalable backend systems.",
    )

    assert route_agency_agents("purple xylophone nebula", root=tmp_path) == []


def test_context_preserves_lead_and_support_roles_and_is_bounded():
    selected = [
        {
            "slug": "security-engineer",
            "name": "Security Engineer",
            "division": "security",
            "routing_role": "lead",
            "selection_reason": "Best overall match",
            "description": "Security testing",
            "content": "x" * 5000,
        },
        {
            "slug": "backend-architect",
            "name": "Backend Architect",
            "division": "engineering",
            "routing_role": "support",
            "selection_reason": "Complementary role",
            "description": "Backend development",
            "content": "y" * 5000,
        },
    ]

    context = render_specialist_context(selected, max_agent_chars=64, max_total_chars=900)

    assert '"routing_role": "lead"' in context
    assert '"routing_role": "support"' in context
    assert len(context) <= 900

