from __future__ import annotations

import asyncio

import pytest

from conftest import wait_for_task


async def _wait_for_goal(client, goal_id: str, timeout: float = 15.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    last = {}
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/goals/{goal_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["state"] not in {"planned", "running"}:
            graph = await client.get(f"/api/goals/{goal_id}/graph")
            assert graph.status_code == 200, graph.text
            last["nodes"] = graph.json()["nodes"]
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"goal did not finish; last state={last.get('state')}")


@pytest.mark.asyncio
async def test_goal_plan_validates_graph_and_enforces_owner_scope(client, anon_client):
    payload = {
        "title": "Invalid goal graph",
        "nodes": [
            {"node_id": "a", "title": "first", "depends_on": ["b"]},
            {"node_id": "b", "title": "second", "depends_on": ["a"]},
        ],
    }
    invalid = await client.post("/api/goals/plan", json=payload)
    assert invalid.status_code == 422

    valid = await client.post(
        "/api/goals/plan",
        json={
            "title": "Owner-only graph",
            "nodes": [{"node_id": "check", "title": "what is my system status?"}],
        },
    )
    assert valid.status_code == 201, valid.text
    goal_id = valid.json()["id"]
    assert valid.json()["execution_status"] == "planned"

    anonymous = await anon_client.get(f"/api/goals/{goal_id}/graph")
    assert anonymous.status_code == 401


@pytest.mark.asyncio
async def test_goal_supervisor_runs_dependency_order_and_waits_for_goal_verification(client):
    planned = await client.post(
        "/api/goals/plan",
        json={
            "title": "Dependency-aware status checks",
            "success_criteria": "Both status checks completed and evidence is present.",
            "nodes": [
                {"node_id": "second", "title": "what is my system status?", "depends_on": ["first"]},
                {"node_id": "first", "title": "what is my system status?"},
            ],
        },
    )
    assert planned.status_code == 201, planned.text
    goal_id = planned.json()["id"]

    started = await client.post(f"/api/goals/{goal_id}/start")
    assert started.status_code == 202, started.text
    finished = await _wait_for_goal(client, goal_id)

    assert finished["state"] == "awaiting_verification"
    assert finished["verification_status"] == "pending_independent_review"
    assert [node["state"] for node in finished["nodes"]] == ["completed", "completed"]
    assert finished["nodes"][0]["task_id"] != finished["nodes"][1]["task_id"]


@pytest.mark.asyncio
async def test_goal_supervisor_continues_independent_branch_and_blocks_failed_descendant(client):
    planned = await client.post(
        "/api/goals/plan",
        json={
            "title": "Independent branch recovery",
            "nodes": [
                {"node_id": "story", "title": "tell me a story about robots"},
                {"node_id": "status", "title": "what is my system status?"},
                {"node_id": "dependent", "title": "publish the result", "depends_on": ["story"]},
            ],
        },
    )
    assert planned.status_code == 201, planned.text
    goal_id = planned.json()["id"]

    started = await client.post(f"/api/goals/{goal_id}/start")
    assert started.status_code == 202, started.text
    finished = await _wait_for_goal(client, goal_id)

    by_id = {node["node_id"]: node for node in finished["nodes"]}
    assert finished["state"] == "partial"
    assert by_id["story"]["state"] == "failed"
    assert by_id["status"]["state"] == "completed"
    assert by_id["dependent"]["state"] == "blocked"


@pytest.mark.asyncio
async def test_goal_cancel_marks_unstarted_nodes_cancelled(client):
    planned = await client.post(
        "/api/goals/plan",
        json={
            "title": "Cancel a planned goal",
            "nodes": [{"node_id": "a", "title": "what is my system status?"}],
        },
    )
    assert planned.status_code == 201, planned.text
    goal_id = planned.json()["id"]
    cancelled = await client.post(f"/api/goals/{goal_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"
    assert cancelled.json()["nodes"][0]["state"] == "cancelled"
