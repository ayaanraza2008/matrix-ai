"""Durable goal lifecycle and task linkage tests."""

from __future__ import annotations

import asyncio

import pytest

from matrix_ai.errors import NotFoundError


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
            "deadline_seconds": 86400,
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


@pytest.mark.asyncio
async def test_model_criteria_review_is_only_a_candidate_until_owner_confirms(client, runtime, monkeypatch):
    from matrix_ai.models.base import ModelResponse

    goal_id = runtime.store.create_goal(
        owner_id="owner",
        title="Check service health",
        success_criteria="The database integrity check must be ok.",
    )
    task_id = runtime.orchestrator.create_task(
        request="Check service health", user_id="owner", kind="goal", goal_id=goal_id
    )
    runtime.store.attach_goal_task(goal_id, task_id)
    runtime.store.update_task(
        task_id,
        state="COMPLETE",
        summary="Database integrity check: ok",
        evidence_json='{"verified":true,"steps":[{"tool":"system.get_status","verified":true}]}',
    )
    runtime.store.sync_goal_for_task(task_id)

    async def fake_complete(messages, **kwargs):
        assert kwargs["json_mode"] is True
        assert "success criteria" in messages[1].content.lower()
        return ModelResponse(
            text='{"outcome":"met","confidence":0.93,"rationale":"The supplied evidence says the database integrity check is ok.","evidence":["Database integrity check: ok"],"missing_evidence":[]}',
            provider="test",
            model="test-model",
            latency_ms=1.0,
        )

    monkeypatch.setattr(runtime.models, "complete", fake_complete)
    evaluated = await client.post(f"/api/goals/{goal_id}/evaluate")
    assert evaluated.status_code == 200, evaluated.text
    candidate = evaluated.json()
    assert candidate["state"] == "awaiting_verification"
    assert candidate["criteria_status"] == "candidate_met"
    assert candidate["criteria_review"]["outcome"] == "met"
    assert candidate["criteria_confirmed_at"] is None

    confirmed = await client.post(
        f"/api/goals/{goal_id}/confirm",
        json={"outcome": "met", "reason": "I checked the reported evidence and confirm it."},
    )
    assert confirmed.status_code == 200, confirmed.text
    final = confirmed.json()
    assert final["state"] == "verified"
    assert final["criteria_status"] == "owner_confirmed_met"
    assert final["criteria_confirmed_by"] == "owner"


@pytest.mark.asyncio
async def test_goal_criteria_can_be_owner_confirmed_without_a_model_review(client, runtime):
    goal_id = runtime.store.create_goal(
        owner_id="owner", title="Private goal", success_criteria="Complete the task"
    )
    task_id = runtime.orchestrator.create_task(
        request="Private goal", user_id="owner", kind="goal", goal_id=goal_id
    )
    runtime.store.attach_goal_task(goal_id, task_id)
    runtime.store.update_task(task_id, state="COMPLETE", evidence_json='{"verified":true}')
    runtime.store.sync_goal_for_task(task_id)

    confirmed = await client.post(
        f"/api/goals/{goal_id}/confirm",
        json={"outcome": "not_met", "reason": "The required output is missing."},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "needs_attention"
    assert confirmed.json()["criteria_review"]["outcome"] == "owner_manual"

    with pytest.raises(NotFoundError):
        runtime.store.confirm_goal_criteria(
            goal_id, owner_id="another-user", outcome="met", reason="Not the owner."
        )


def test_schema_v3_goals_migrate_criteria_review_metadata(tmp_path):
    from matrix_ai.storage.db import Database

    path = tmp_path / "schema-v3.sqlite"
    initial = Database(path)
    for column in (
        "criteria_review_json",
        "criteria_reviewed_at",
        "criteria_confirmed_at",
        "criteria_confirmed_by",
        "criteria_confirmation_reason",
    ):
        initial.execute(f"ALTER TABLE goals DROP COLUMN {column}")
    initial.execute("PRAGMA user_version=3")
    initial.close()

    upgraded = Database(path)
    columns = {row["name"] for row in upgraded.query("PRAGMA table_info(goals)")}
    assert {
        "criteria_review_json",
        "criteria_reviewed_at",
        "criteria_confirmed_at",
        "criteria_confirmed_by",
        "criteria_confirmation_reason",
    } <= columns
    assert upgraded.integrity_check() == "ok"
    upgraded.close()


@pytest.mark.asyncio
async def test_completed_goal_gets_advisory_review_automatically(runtime, monkeypatch):
    from matrix_ai.models.base import ModelResponse

    goal_id = runtime.store.create_goal(
        owner_id="owner", title="Verify disk report", success_criteria="A disk report is present."
    )
    task_id = runtime.orchestrator.create_task(
        request="Verify disk report", user_id="owner", kind="goal", goal_id=goal_id
    )
    runtime.store.attach_goal_task(goal_id, task_id)
    runtime.store.update_task(
        task_id,
        state="COMPLETE",
        summary="Disk report was generated.",
        evidence_json='{"verified":true,"steps":[{"tool":"files.write_text","verified":true}]}',
    )
    runtime.store.sync_goal_for_task(task_id)

    async def fake_complete(messages, **kwargs):
        return ModelResponse(
            text='{"outcome":"met","confidence":0.8,"rationale":"The task evidence reports the disk report was generated.","evidence":["Disk report was generated."],"missing_evidence":[]}',
            provider="test",
            model="test-model",
            latency_ms=1.0,
        )

    monkeypatch.setattr(runtime.models, "complete", fake_complete)
    await runtime.orchestrator._evaluate_goal_candidate(task_id)
    goal = runtime.store.get_goal(goal_id)
    assert goal["criteria_status"] == "candidate_met"
    assert goal["state"] == "awaiting_verification"
    assert goal["criteria_confirmed_at"] is None


@pytest.mark.asyncio
async def test_goal_graph_dispatches_roots_and_unlocks_dependents_after_owner_confirmation(client, runtime, monkeypatch):
    async def no_enqueue(_task_id):
        return None

    monkeypatch.setattr(runtime.orchestrator, "enqueue", no_enqueue)
    response = await client.post(
        "/api/goal-graphs",
        json={
            "title": "Prepare then publish",
            "nodes": [
                {"node_id": "publish", "title": "Publish", "success_criteria": "The post is published.", "depends_on": ["prepare"]},
                {"node_id": "prepare", "title": "Prepare", "success_criteria": "The draft is prepared."},
                {"node_id": "independent", "title": "Independent check", "success_criteria": "The check is complete."},
            ],
        },
    )
    assert response.status_code == 202, response.text
    graph = response.json()
    by_id = {node["node_id"]: node for node in graph["nodes"]}
    assert by_id["prepare"]["goal"]["task_id"]
    assert by_id["independent"]["goal"]["task_id"]
    assert by_id["publish"]["goal"]["task_id"] is None
    assert graph["state"] == "running"

    for node_id in ("prepare", "independent"):
        goal_id = by_id[node_id]["goal_id"]
        task_id = by_id[node_id]["goal"]["task_id"]
        runtime.store.update_task(
            task_id,
            state="COMPLETE",
            summary=f"{node_id} finished",
            evidence_json='{"verified":true,"steps":[{"verified":true}]}',
        )
        runtime.store.sync_goal_for_task(task_id)
        confirmed = await client.post(
            f"/api/goals/{goal_id}/confirm",
            json={"outcome": "met", "reason": f"I reviewed the {node_id} evidence."},
        )
        assert confirmed.status_code == 200, confirmed.text

    latest = await client.get(f"/api/goal-graphs/{graph['id']}")
    assert latest.status_code == 200, latest.text
    refreshed = {node["node_id"]: node for node in latest.json()["nodes"]}
    assert refreshed["prepare"]["goal"]["state"] == "verified"
    assert refreshed["independent"]["goal"]["state"] == "verified"
    assert refreshed["publish"]["goal"]["task_id"]
    assert refreshed["publish"]["goal"]["state"] == "running"


@pytest.mark.asyncio
async def test_goal_graph_rejects_cycles_and_hides_other_owners(client, runtime):
    invalid = await client.post(
        "/api/goal-graphs",
        json={
            "title": "Cycle",
            "nodes": [
                {"node_id": "a", "title": "A", "success_criteria": "A done", "depends_on": ["b"]},
                {"node_id": "b", "title": "B", "success_criteria": "B done", "depends_on": ["a"]},
            ],
        },
    )
    assert invalid.status_code == 422

    graph_id = runtime.store.create_goal_graph(
        owner_id="another-user",
        title="Private graph",
        nodes=[{"node_id": "one", "title": "Private", "success_criteria": "Done", "depends_on": []}],
    )
    hidden = await client.get(f"/api/goal-graphs/{graph_id}")
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_goal_graph_failure_blocks_only_descendants_and_runs_independent_nodes(runtime, monkeypatch):
    async def no_enqueue(_task_id):
        return None

    monkeypatch.setattr(runtime.orchestrator, "enqueue", no_enqueue)
    graph_id = runtime.store.create_goal_graph(
        owner_id="owner",
        title="Failure isolation",
        nodes=[
            {"node_id": "root", "title": "Root", "success_criteria": "Root succeeds", "depends_on": []},
            {"node_id": "dependent", "title": "Dependent", "success_criteria": "Depends on root", "depends_on": ["root"]},
            {"node_id": "independent", "title": "Independent", "success_criteria": "Independent succeeds", "depends_on": []},
        ],
    )
    graph = runtime.store.get_goal_graph(graph_id)
    by_id = {node["node_id"]: node for node in graph["nodes"]}
    runtime.store.db.execute(
        "UPDATE goals SET state='failed', result_summary='root failed' WHERE id=?",
        (by_id["root"]["goal_id"],),
    )

    await runtime.goal_supervisor.tick()
    refreshed = {node["node_id"]: node for node in runtime.store.get_goal_graph(graph_id)["nodes"]}
    assert refreshed["dependent"]["goal"]["state"] == "blocked"
    assert refreshed["independent"]["goal"]["state"] == "running"
    assert refreshed["independent"]["goal"]["task_id"]


def test_schema_v4_migrates_goal_graph_tables(tmp_path):
    from matrix_ai.storage.db import Database

    path = tmp_path / "schema-v4.sqlite"
    initial = Database(path)
    initial.execute("DROP TABLE goal_graph_nodes")
    initial.execute("DROP TABLE goal_graphs")
    initial.execute("PRAGMA user_version=4")
    initial.close()

    upgraded = Database(path)
    tables = {
        row["name"]
        for row in upgraded.query("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"goal_graphs", "goal_graph_nodes"} <= tables
    assert upgraded.integrity_check() == "ok"
    upgraded.close()


@pytest.mark.asyncio
async def test_goal_supervisor_enforces_total_graph_concurrency(runtime, monkeypatch):
    async def no_enqueue(_task_id):
        return None

    monkeypatch.setattr(runtime.orchestrator, "enqueue", no_enqueue)
    graph_id = runtime.store.create_goal_graph(
        owner_id="owner",
        title="Bounded parallel work",
        nodes=[
            {"node_id": "a", "title": "A", "success_criteria": "A done", "depends_on": []},
            {"node_id": "b", "title": "B", "success_criteria": "B done", "depends_on": []},
            {"node_id": "c", "title": "C", "success_criteria": "C done", "depends_on": []},
        ],
    )
    await runtime.goal_supervisor.tick()
    nodes = runtime.store.get_goal_graph(graph_id)["nodes"]
    assert sum(node["goal"]["state"] == "running" for node in nodes) == 2
    assert sum(node["goal"]["state"] == "queued" for node in nodes) == 1
