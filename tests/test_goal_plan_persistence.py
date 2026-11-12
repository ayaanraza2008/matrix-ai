from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_goal_plan_is_persisted_without_starting_execution(client, runtime):
    response = await client.post(
        "/api/goals/plan",
        json={
            "title": "Prepare a verified video release",
            "success_criteria": "All plan nodes finish and the release is independently checked.",
            "nodes": [
                {"node_id": "publish", "title": "Publish after approval", "depends_on": ["review"]},
                {"node_id": "prepare", "title": "Prepare draft"},
                {"node_id": "review", "title": "Review draft", "depends_on": ["prepare"]},
            ],
        },
    )
    assert response.status_code == 201, response.text
    goal = response.json()
    assert goal["state"] == "planned"
    assert goal["task_id"] is None
    assert goal["execution_status"] == "plan_only"
    assert [node["node_id"] for node in goal["nodes"]] == ["publish", "prepare", "review"]
    assert goal["nodes"][0]["depends_on"] == ["review"]
    assert runtime.store.get_goal_graph(goal["id"])[2]["depends_on"] == ["prepare"]


@pytest.mark.asyncio
async def test_goal_plan_rejects_cycles_before_persisting(client, runtime):
    response = await client.post(
        "/api/goals/plan",
        json={
            "title": "Cyclic plan",
            "nodes": [
                {"node_id": "a", "title": "A", "depends_on": ["b"]},
                {"node_id": "b", "title": "B", "depends_on": ["a"]},
            ],
        },
    )
    assert response.status_code == 422
    assert "cycle" in response.json()["detail"]
    assert runtime.store.db.query_one("SELECT COUNT(*) AS c FROM goals")[0] == 0


@pytest.mark.asyncio
async def test_goal_plan_graph_read_is_owner_scoped(client, runtime):
    goal_id = runtime.store.create_goal(
        owner_id="another-user", title="Private plan", success_criteria="Done"
    )
    response = await client.get(f"/api/goals/{goal_id}/graph")
    assert response.status_code == 404


def test_goal_node_schema_migrates_and_is_indexed(tmp_path):
    from matrix_ai.storage.db import Database

    path = tmp_path / "goal-nodes.sqlite"
    db = Database(path)
    columns = {row["name"] for row in db.query("PRAGMA table_info(goal_nodes)")}
    assert {
        "goal_id", "node_id", "title", "depends_on_json", "state",
        "task_id", "attempt_count", "error", "evidence_json",
    } <= columns
    assert db.integrity_check() == "ok"
    db.close()


@pytest.mark.asyncio
async def test_start_goal_plan_dispatches_only_dependency_ready_nodes(client, runtime, monkeypatch):
    from unittest.mock import AsyncMock

    enqueue = AsyncMock()
    monkeypatch.setattr(runtime.orchestrator, "enqueue", enqueue)
    created = await client.post(
        "/api/goals/plan",
        json={
            "title": "Ship a release",
            "nodes": [
                {"node_id": "publish", "title": "Publish", "depends_on": ["review"]},
                {"node_id": "prepare", "title": "Prepare"},
                {"node_id": "review", "title": "Review", "depends_on": ["prepare"]},
                {"node_id": "independent", "title": "Independent check"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]

    started = await client.post(f"/api/goals/{goal_id}/start")
    assert started.status_code == 202, started.text
    body = started.json()
    states = {node["node_id"]: node["state"] for node in body["nodes"]}
    assert states == {
        "publish": "pending",
        "prepare": "running",
        "review": "pending",
        "independent": "running",
    }
    assert len(body["dispatched_task_ids"]) == 2
    assert enqueue.await_count == 2

    tasks = runtime.store.db.query(
        "SELECT goal_node_id FROM tasks WHERE goal_id=? ORDER BY goal_node_id",
        (goal_id,),
    )
    assert {row["goal_node_id"] for row in tasks} == {"prepare", "independent"}


@pytest.mark.asyncio
async def test_failed_goal_node_blocks_all_descendants(client, runtime):
    created = await client.post(
        "/api/goals/plan",
        json={
            "title": "Dependency failure",
            "nodes": [
                {"node_id": "root", "title": "Root"},
                {"node_id": "child", "title": "Child", "depends_on": ["root"]},
                {"node_id": "grandchild", "title": "Grandchild", "depends_on": ["child"]},
                {"node_id": "parallel", "title": "Parallel"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    runtime.store.db.execute(
        "UPDATE goal_nodes SET state='failed' WHERE goal_id=? AND node_id='root'",
        (goal_id,),
    )
    assert runtime.store.refresh_blocked_goal_nodes(goal_id) == 2
    ready = runtime.store.ready_goal_nodes(goal_id)
    assert [node["node_id"] for node in ready] == ["parallel"]
    states = {node["node_id"]: node["state"] for node in runtime.store.get_goal_graph(goal_id)}
    assert states["child"] == "blocked"
    assert states["grandchild"] == "blocked"


@pytest.mark.asyncio
async def test_start_goal_plan_is_owner_scoped(client, runtime):
    goal_id = runtime.store.create_goal(
        owner_id="different-owner", title="Private plan", success_criteria="Done"
    )
    runtime.store.save_goal_graph(
        goal_id, [{"node_id": "private", "title": "Private", "depends_on": []}]
    )
    response = await client.post(f"/api/goals/{goal_id}/start")
    assert response.status_code == 404


def test_task_goal_node_identity_is_unique_and_persisted(tmp_path):
    from matrix_ai.storage.db import Database
    from matrix_ai.storage.store import Store

    db = Database(tmp_path / "goal-node-task.sqlite")
    store = Store(db)
    store.ensure_user("owner")
    goal_id = store.create_goal(owner_id="owner", title="Goal", success_criteria="Done")
    store.save_goal_graph(
        goal_id, [{"node_id": "step", "title": "Step", "depends_on": []}]
    )
    task_id = store.create_task(
        user_id="owner", original_request="Step", kind="goal_node",
        goal_id=goal_id, goal_node_id="step",
    )
    store.attach_goal_node_task(goal_id, "step", task_id)
    assert store.get_task_row(task_id)["goal_node_id"] == "step"
    assert store.get_goal_node_task(goal_id, "step")["id"] == task_id
    with pytest.raises(Exception):
        store.create_task(
            user_id="owner", original_request="Duplicate", kind="goal_node",
            goal_id=goal_id, goal_node_id="step",
        )
    db.close()
