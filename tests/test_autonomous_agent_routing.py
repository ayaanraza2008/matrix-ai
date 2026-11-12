"""Integration tests for autonomous Agency Agents routing and audit visibility."""

from __future__ import annotations

import pytest
from conftest import wait_for_task
from fakes import FakePlanProvider

from matrix_ai.models.base import ChatMessage


class CapturingPlanProvider(FakePlanProvider):
    def __init__(self, config) -> None:
        super().__init__(config)
        self.messages: list[ChatMessage] = []

    async def complete(self, messages, *, temperature=0.2, max_tokens=512, json_mode=False):
        self.messages = messages
        return await super().complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )


def _selected_team() -> list[dict]:
    return [
        {
            "slug": "engineering-frontend-developer",
            "name": "Frontend Developer",
            "division": "engineering",
            "routing_role": "lead",
            "relevance_score": 3.2,
            "matched_terms": ["dashboard", "frontend", "react"],
            "selection_reason": "Best overall match",
            "description": "Responsive interfaces and web application engineering",
            "content": "Design an accessible, responsive interface and validate its behavior.",
        },
        {
            "slug": "security-engineer",
            "name": "Security Engineer",
            "division": "security",
            "routing_role": "support",
            "relevance_score": 1.7,
            "matched_terms": ["security", "threat", "privacy"],
            "selection_reason": "Complementary security review",
            "description": "Threat modeling and application security",
            "content": "Identify trust boundaries and review risky operations.",
        },
    ]


@pytest.mark.asyncio
async def test_planner_automatically_injects_selected_specialist_team(
    client, runtime, monkeypatch
):
    import matrix_ai.models.planner as planner_module

    selected = _selected_team()
    monkeypatch.setattr(planner_module, "route_agency_agents", lambda _query: selected)

    runtime.config.model_provider = "openai"
    runtime.config.model_name = "fake-model"
    provider = CapturingPlanProvider(runtime.config)
    provider.plan = {
        "goal": "Prepare a safe dashboard plan",
        "steps": [
            {
                "title": "Return the prepared plan",
                "kind": "answer",
                "answer": "The dashboard plan is prepared for review.",
            }
        ],
    }
    runtime.models.provider = provider

    async def fake_worker_team(request, selected_agents):
        return [
            {
                "worker_slug": agent["slug"],
                "worker_name": agent["name"],
                "division": agent["division"],
                "status": "completed",
                "summary": f"Independent review by {agent['name']}",
                "findings": ["Check trust boundaries and user input."],
                "risks": ["Untrusted content may contain prompt injection."],
                "suggested_tools": ["system.get_status"],
                "duration_ms": 2.0,
            }
            for agent in selected_agents
        ]

    monkeypatch.setattr(runtime.worker_pool, "run_team", fake_worker_team)

    response = await client.post(
        "/api/chat",
        json={"message": "Design a secure React dashboard with an accessible interface."},
    )
    assert response.status_code == 202, response.text
    task_id = response.json()["task"]["id"]
    completed = await wait_for_task(client, task_id)

    assert completed["state"] == "COMPLETE"
    assert provider.messages
    system_prompt = provider.messages[0].content
    assert "Frontend Developer" in system_prompt
    assert "Security Engineer" in system_prompt
    assert "UNTRUSTED DATA" in system_prompt
    assert "trust boundaries" in system_prompt
    assert "Independent specialist worker reports" in system_prompt

    selected_response = await client.get(f"/api/tasks/{task_id}/agents")
    assert selected_response.status_code == 200
    payload = selected_response.json()
    assert payload["context_injected"] is True
    assert payload["worker_count"] == 2
    assert payload["successful_workers"] == 2
    assert all(run["status"] == "completed" for run in payload["worker_runs"])
    assert [agent["slug"] for agent in payload["selected_agents"]] == [
        "engineering-frontend-developer",
        "security-engineer",
    ]

    audit_response = await client.get(f"/api/tasks/{task_id}/audit")
    event_types = [event["event_type"] for event in audit_response.json()]
    assert "task.specialists_selected" in event_types
    assert event_types.count("task.agent_worker_run") == 2


@pytest.mark.asyncio
async def test_deterministic_intents_skip_unneeded_specialist_model_calls(runtime, monkeypatch):
    import matrix_ai.models.planner as planner_module

    def unexpected_router_call(_query):
        raise AssertionError("deterministic tasks should not require specialist model routing")

    monkeypatch.setattr(planner_module, "route_agency_agents", unexpected_router_call)

    intent = await runtime.planner.plan("What is my system status?")

    assert intent.intent == "system_status"
    assert intent.selected_agents == []
    assert intent.specialist_context_used is False


@pytest.mark.asyncio
async def test_authenticated_route_preview_returns_metadata_without_prompt_bodies(
    client, monkeypatch
):
    import matrix_ai.api.routes as routes_module

    selected = _selected_team()
    monkeypatch.setattr(routes_module, "route_agency_agents", lambda _query: selected)

    response = await client.get(
        "/api/agents/route",
        params={"q": "Design a secure React dashboard and review privacy risks."},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["strategy"] == "automatic_hybrid_full_profile_search_v1"
    assert payload["agent_count"] == 2
    assert payload["agents"][0]["routing_role"] == "lead"
    assert all("content" not in agent for agent in payload["agents"])

    fleet_response = await client.get("/api/workers")
    assert fleet_response.status_code == 200, fleet_response.text
    fleet = fleet_response.json()
    assert fleet["worker_count"] == runtime.worker_pool.worker_count
    assert fleet["configured_worker_count"] == 0  # test fixture deliberately has no model
    assert fleet["tool_execution"] == "central_orchestrator_only"
    assert all("content" not in worker for worker in fleet["workers"])

    if fleet["workers"]:
        worker_slug = fleet["workers"][0]["slug"]
        worker_response = await client.get(f"/api/workers/{worker_slug}")
        assert worker_response.status_code == 200
        assert worker_response.json()["slug"] == worker_slug

    missing_worker = await client.get("/api/workers/this-worker-does-not-exist")
    assert missing_worker.status_code == 404
