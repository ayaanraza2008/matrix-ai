from __future__ import annotations

import asyncio

import pytest


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
    assert finished["verification_status"] == "pending_owner_review"
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


@pytest.mark.asyncio
async def test_goal_criteria_second_pass_can_verify_completed_nodes(client, runtime, monkeypatch):
    from types import SimpleNamespace

    async def fake_complete(messages, **kwargs):
        return SimpleNamespace(
            text='{"status":"verified","reason":"The status evidence supports the stated criteria.","supporting_nodes":["status"],"missing_evidence":[]}'
        )

    monkeypatch.setattr(type(runtime.models), "configured", property(lambda _self: True))
    monkeypatch.setattr(runtime.models, "complete", fake_complete)

    planned = await client.post(
        "/api/goals/plan",
        json={
            "title": "Verify a status goal",
            "success_criteria": "The system status check completed successfully.",
            "nodes": [{"node_id": "status", "title": "what is my system status?"}],
        },
    )
    assert planned.status_code == 201, planned.text
    goal_id = planned.json()["id"]
    started = await client.post(f"/api/goals/{goal_id}/start")
    assert started.status_code == 202, started.text
    finished = await _wait_for_goal(client, goal_id)

    assert finished["state"] == "completed"
    assert finished["criteria_status"] == "verified_by_model"
    assert finished["verification_status"] == "second_pass_model_assessed"


@pytest.mark.asyncio
async def test_goal_supervisor_recovers_orphaned_dispatch_claim(client, runtime):
    planned = await client.post(
        "/api/goals/plan",
        json={
            "title": "Recover a dispatch claim",
            "nodes": [{"node_id": "status", "title": "what is my system status?"}],
        },
    )
    assert planned.status_code == 201, planned.text
    goal_id = planned.json()["id"]

    assert runtime.store.claim_goal_node(goal_id, "status") is True
    assert runtime.store.get_goal_graph(goal_id)[0]["state"] == "dispatching"

    recovered = await runtime.goal_supervisor.recover_after_restart()
    assert recovered["resumed"] == 1
    runner = runtime.goal_supervisor._runners[goal_id]
    await runner

    result = runtime.goal_supervisor.status(goal_id)
    assert result["state"] == "awaiting_verification"
    assert result["nodes"][0]["state"] == "completed"
    assert result["nodes"][0]["task_id"]


@pytest.mark.asyncio
async def test_goal_node_task_identity_repairs_create_link_crash_window(client, runtime):
    planned = await client.post(
        "/api/goals/plan",
        json={
            "title": "Repair task linkage",
            "nodes": [{"node_id": "status", "title": "what is my system status?"}],
        },
    )
    assert planned.status_code == 201, planned.text
    goal_id = planned.json()["id"]
    owner_id = planned.json()["owner_id"]

    assert runtime.store.claim_goal_node(goal_id, "status") is True
    task_id = runtime.orchestrator.create_task(
        request="goal node status check",
        user_id=owner_id,
        kind="goal_node",
        goal_id=goal_id,
        goal_node_id="status",
    )
    assert runtime.store.get_goal_graph(goal_id)[0]["task_id"] is None

    runtime.store.recover_goal_graphs()
    node = runtime.store.get_goal_graph(goal_id)[0]
    assert node["task_id"] == task_id
    assert node["state"] == "running"

    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        runtime.orchestrator.create_task(
            request="duplicate node task",
            user_id=owner_id,
            kind="goal_node",
            goal_id=goal_id,
            goal_node_id="status",
        )


@pytest.mark.asyncio
async def test_goal_node_claims_respect_four_node_concurrency_bound(client, runtime):
    planned = await client.post(
        "/api/goals/plan",
        json={
            "title": "Bounded ready wave",
            "nodes": [
                {"node_id": f"node-{i}", "title": "what is my system status?"}
                for i in range(5)
            ],
        },
    )
    assert planned.status_code == 201, planned.text
    goal_id = planned.json()["id"]

    for i in range(4):
        assert runtime.store.claim_goal_node(goal_id, f"node-{i}", limit=4) is True
    assert runtime.store.claim_goal_node(goal_id, "node-4", limit=4) is False


@pytest.mark.asyncio
async def test_goal_cancellation_closes_dispatch_gate_before_cancelling_tasks(client, runtime):
    planned = await client.post(
        "/api/goals/plan",
        json={
            "title": "Stop further dispatch",
            "nodes": [
                {"node_id": "first", "title": "what is my system status?"},
                {"node_id": "second", "title": "what is my system status?"},
            ],
        },
    )
    assert planned.status_code == 201, planned.text
    goal_id = planned.json()["id"]
    assert runtime.store.claim_goal_node(goal_id, "first") is True

    task_ids = runtime.store.begin_cancel_goal_graph(goal_id)
    assert task_ids == []
    assert runtime.store.claim_goal_node(goal_id, "second") is False
    runtime.store.finish_goal_graph(goal_id, state="cancelled", summary="Cancelled in test.")
    assert runtime.store.get_goal(goal_id)["state"] == "cancelled"
