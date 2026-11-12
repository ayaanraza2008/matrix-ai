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
