"""Durable goal lifecycle and task linkage tests."""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_goal_api_persists_task_link_and_terminal_result(client):
    response = await client.post(
        "/api/goals",
        json={
            "title": "What is my system status?",
            "success_criteria": "Return the current system status and verify completion.",
        },
    )
    assert response.status_code == 202, response.text
    created = response.json()
    assert created["id"]
    assert created["state"] in {"running", "completed"}
    assert created["task_id"]
    assert created["task"]["goal_id"] == created["id"]

    terminal = {"completed", "partial", "failed", "cancelled"}
    latest = created
    for _ in range(200):
        check = await client.get(f"/api/goals/{created['id']}")
        assert check.status_code == 200, check.text
        latest = check.json()
        if latest["state"] in terminal:
            break
        await asyncio.sleep(0.025)

    assert latest["state"] == "completed"
    assert latest["finished_at"]
    assert latest["result_summary"]


@pytest.mark.asyncio
async def test_goal_list_is_scoped_to_authenticated_owner(client, runtime):
    own_id = runtime.store.create_goal(
        owner_id="owner", title="Owner goal", success_criteria="Done"
    )
    runtime.store.create_goal(
        owner_id="another-user", title="Private goal", success_criteria="Done"
    )

    response = await client.get("/api/goals")
    assert response.status_code == 200, response.text
    ids = {goal["id"] for goal in response.json()}
    assert own_id in ids
    assert len(ids) == 1


@pytest.mark.asyncio
async def test_goal_cannot_be_attached_to_multiple_tasks(runtime):
    goal_id = runtime.store.create_goal(
        owner_id="owner", title="Single execution", success_criteria="Done"
    )
    first = runtime.orchestrator.create_task(
        request="tell me a story", user_id="owner", kind="goal", goal_id=goal_id
    )
    second = runtime.orchestrator.create_task(
        request="what is my system status again?", user_id="owner", kind="goal", goal_id=goal_id
    )

    runtime.store.attach_goal_task(goal_id, first)
    with pytest.raises(ValueError, match="already attached"):
        runtime.store.attach_goal_task(goal_id, second)

    assert runtime.store.get_goal(goal_id)["task_id"] == first


@pytest.mark.asyncio
async def test_terminal_task_state_is_reflected_in_goal(runtime):
    goal_id = runtime.store.create_goal(
        owner_id="owner", title="Task linkage", success_criteria="Done"
    )
    task_id = runtime.orchestrator.create_task(
        request="what is my system status?", user_id="owner", kind="goal", goal_id=goal_id
    )
    runtime.store.attach_goal_task(goal_id, task_id)
    await runtime.orchestrator.enqueue(task_id)

    for _ in range(200):
        task = runtime.store.get_task_row(task_id)
        if task["state"] in {"COMPLETE", "PARTIAL", "FAILED", "CANCELLED"}:
            break
        await asyncio.sleep(0.025)

    goal = runtime.store.get_goal(goal_id)
    assert goal["state"] == "completed"
    assert goal["finished_at"]
