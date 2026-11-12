"""A05 - destructive actions require exact, per-action approval."""

from __future__ import annotations

import pytest
from conftest import create_workspace_file, execute_tool, wait_for_state, wait_for_task

from matrix_ai.core.models import action_fingerprint

pytestmark = pytest.mark.acceptance


async def test_delete_waits_for_exact_owner_approval(client, runtime):
    created = await create_workspace_file(client, "approval.txt", "keep me")
    assert created["state"] == "COMPLETE"

    task = await execute_tool(client, "files.delete", {"path": "approval.txt"})
    waiting = await wait_for_state(client, task["id"], {"WAITING_FOR_USER"})
    assert waiting["state"] == "WAITING_FOR_USER"
    approvals = waiting["approvals"]
    assert approvals and approvals[-1]["status"] == "pending"
    approval = approvals[-1]
    assert approval["tool_name"] == "files.delete"
    assert approval["arguments"]["path"].endswith("approval.txt")
    # Nothing may be executed before approval.
    assert (runtime.config.workspace_dir / "approval.txt").exists()
    assert waiting["tool_calls"] == []

    response = await client.post(
        f"/api/tasks/{task['id']}/approvals/{approval['id']}/approve", json={}
    )
    assert response.status_code == 200
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "COMPLETE"
    assert not (runtime.config.workspace_dir / "approval.txt").exists()
    assert done["approvals"][-1]["status"] == "approved"


async def test_denied_approval_executes_nothing(client, runtime):
    await create_workspace_file(client, "denied.txt", "still here")
    task = await execute_tool(client, "files.delete", {"path": "denied.txt"})
    waiting = await wait_for_state(client, task["id"], {"WAITING_FOR_USER"})
    approval = waiting["approvals"][-1]

    response = await client.post(
        f"/api/tasks/{task['id']}/approvals/{approval['id']}/deny",
        json={"reason": "not now"},
    )
    assert response.status_code == 200
    done = await wait_for_task(client, task["id"])
    assert done["state"] in {"FAILED", "CANCELLED"}
    assert (runtime.config.workspace_dir / "denied.txt").exists()
    assert done["approvals"][-1]["status"] == "denied"
    audit = await client.get(f"/api/tasks/{task['id']}/audit")
    assert "approval.owner_denied" in [event["event_type"] for event in audit.json()]


async def test_approval_is_bound_to_exact_arguments():
    first = action_fingerprint("files.delete", {"path": "a.txt"})
    second = action_fingerprint("files.delete", {"path": "b.txt"})
    assert first != second
    assert first == action_fingerprint("files.delete", {"path": "a.txt"})


async def test_expired_approval_cannot_be_used(runtime):
    task_id = runtime.orchestrator.create_direct_tool_task("files.delete", {"path": "ghost.txt"})
    approval_id = runtime.store.create_approval(
        task_id=task_id,
        step_idx=0,
        tool_name="files.delete",
        arguments={"path": "ghost.txt"},
        action_hash="deadbeef",
        risk=runtime.registry.get("files.delete").risk,
        ttl_seconds=-5,
    )
    status = runtime.store.decide_approval(approval_id, approved=True)
    assert status == "expired"
    assert runtime.store.get_approval_row(approval_id)["status"] == "expired"


async def test_high_risk_write_requires_approval(client, runtime):
    await create_workspace_file(client, "existing.txt", "v1")
    task = await execute_tool(
        client, "files.write_text", {"path": "existing.txt", "content": "v2", "overwrite": True}
    )
    waiting = await wait_for_state(client, task["id"], {"WAITING_FOR_USER"})
    assert waiting["approvals"][-1]["risk"] == "high"
    assert (runtime.config.workspace_dir / "existing.txt").read_text() == "v1"
