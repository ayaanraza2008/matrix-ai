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
    assert created["state"] in {"running", "awaiting_verification"}
    assert created["task_id"]
    assert created["task"]["goal_id"] == created["id"]

    terminal = {"awaiting_verification", "partial", "failed", "cancelled"}
    latest = created
    for _ in range(200):
        check = await client.get(f"/api/goals/{created['id']}")
        assert check.status_code == 200, check.text
        latest = check.json()
        if latest["state"] in terminal:
            break
        await asyncio.sleep(0.025)

    assert latest["state"] == "awaiting_verification"
    assert latest["finished_at"]
    assert latest["result_summary"]
    assert latest["verification_status"] == "task_steps_verified"
    # Semantic success criteria are not yet evaluated; never infer them from task completion.
    assert latest["criteria_status"] == "not_evaluated"
    assert latest["evidence_json"]


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
    assert goal["state"] == "awaiting_verification"
    assert goal["finished_at"]


def test_schema_v1_database_migrates_to_goal_linkage(tmp_path):
    from matrix_ai.storage.db import Database

    path = tmp_path / "legacy.sqlite"
    initial = Database(path)
    initial.execute("DROP INDEX IF EXISTS idx_tasks_goal")
    initial.execute("ALTER TABLE tasks DROP COLUMN goal_id")
    initial.execute("DROP TABLE goals")
    initial.execute("PRAGMA user_version=1")
    initial.close()

    upgraded = Database(path)
    columns = {row["name"] for row in upgraded.query("PRAGMA table_info(tasks)")}
    assert "goal_id" in columns
    assert upgraded.query_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='goals'"
    )
    assert upgraded.integrity_check() == "ok"
    upgraded.close()


@pytest.mark.asyncio
async def test_startup_recovery_resumes_received_goal_task(runtime):
    goal_id = runtime.store.create_goal(
        owner_id="owner", title="Recover my status check", success_criteria="Verify current status"
    )
    task_id = runtime.orchestrator.create_task(
        request="what is my system status?", user_id="owner", kind="goal", goal_id=goal_id
    )
    runtime.store.attach_goal_task(goal_id, task_id)

    recovered = await runtime.orchestrator.recover_after_restart()
    assert recovered["resumed"] == 1

    for _ in range(200):
        goal = runtime.store.get_goal(goal_id)
        if goal["state"] in {"awaiting_verification", "partial", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.025)

    assert goal["state"] == "awaiting_verification"


@pytest.mark.asyncio
async def test_owner_can_cancel_goal_waiting_for_approval(client):
    response = await client.post(
        "/api/goals",
        json={
            "title": "Delete file 'important.txt'",
            "success_criteria": "Delete only after explicit approval.",
        },
    )
    assert response.status_code == 202, response.text
    goal = response.json()

    task_state = None
    for _ in range(200):
        task_response = await client.get(f"/api/tasks/{goal['task_id']}")
        assert task_response.status_code == 200, task_response.text
        task_state = task_response.json()["state"]
        if task_state == "WAITING_FOR_USER":
            break
        if task_state in {"COMPLETE", "PARTIAL", "FAILED", "CANCELLED"}:
            pytest.fail(f"high-risk delete unexpectedly ended in {task_state}")
        await asyncio.sleep(0.025)

    assert task_state == "WAITING_FOR_USER"
    cancelled = await client.post(f"/api/goals/{goal['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"


@pytest.mark.asyncio
async def test_goal_deadline_is_persisted_and_bounded_by_runtime_config(client, runtime):
    response = await client.post(
        "/api/goals",
        json={
            "title": "Check system health",
            "success_criteria": "Return verified system health information.",
            "deadline_seconds": 10,
        },
    )
    assert response.status_code == 202, response.text
    goal = response.json()
    assert goal["deadline"]
    task = runtime.store.get_task_row(goal["task_id"])
    assert task["deadline"] == goal["deadline"]


def test_schema_v2_goals_migrate_verification_metadata(tmp_path):
    from matrix_ai.storage.db import Database

    path = tmp_path / "schema-v2.sqlite"
    initial = Database(path)
    for column in ("deadline", "verification_status", "criteria_status", "evidence_json"):
        initial.execute(f"ALTER TABLE goals DROP COLUMN {column}")
    initial.execute("PRAGMA user_version=2")
    initial.close()

    upgraded = Database(path)
    columns = {row["name"] for row in upgraded.query("PRAGMA table_info(goals)")}
    assert {"deadline", "verification_status", "criteria_status", "evidence_json"} <= columns
    assert upgraded.integrity_check() == "ok"
    upgraded.close()


def test_goal_cannot_attach_another_users_task(runtime):
    goal_id = runtime.store.create_goal(
        owner_id="owner", title="Owner-only goal", success_criteria="Done"
    )
    task_id = runtime.orchestrator.create_task(
        request="what is my system status?",
        user_id="another-user",
        kind="goal",
        goal_id=goal_id,
    )
    with pytest.raises(ValueError, match="owner does not match"):
        runtime.store.attach_goal_task(goal_id, task_id)
    assert runtime.store.get_goal(goal_id)["task_id"] is None
