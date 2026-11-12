"""A20 - reconnect/replay safety: no duplicate task execution."""

from __future__ import annotations

import asyncio

import pytest
from conftest import create_workspace_file, execute_tool, wait_for_state, wait_for_task

pytestmark = pytest.mark.acceptance


async def test_double_enqueue_executes_once(client, runtime):
    task = await execute_tool(client, "system.get_status", {})
    task_id = task["id"]
    # A reconnecting client may re-send the same task id/request.
    await asyncio.gather(
        runtime.orchestrator.enqueue(task_id),
        runtime.orchestrator.enqueue(task_id),
        runtime.orchestrator.enqueue(task_id),
    )
    done = await wait_for_task(client, task_id)
    assert done["state"] == "COMPLETE"
    succeeded = [call for call in done["tool_calls"] if call["status"] == "succeeded"]
    assert len(succeeded) == 1


async def test_double_approval_executes_once(client, runtime):
    await create_workspace_file(client, "reconnect.txt", "once")
    task = await execute_tool(client, "files.delete", {"path": "reconnect.txt"})
    waiting = await wait_for_state(client, task["id"], {"WAITING_FOR_USER"})
    approval_id = waiting["approvals"][-1]["id"]

    first, second = await asyncio.gather(
        client.post(f"/api/tasks/{task['id']}/approvals/{approval_id}/approve", json={}),
        client.post(f"/api/tasks/{task['id']}/approvals/{approval_id}/approve", json={}),
    )
    assert first.status_code == 200
    assert second.status_code == 200

    done = await wait_for_task(client, task["id"])
    assert done["state"] == "COMPLETE"
    approvals = done["approvals"]
    assert [entry["status"] for entry in approvals].count("approved") == 1
    delete_calls = [call for call in done["tool_calls"] if call["tool_name"] == "files.delete"]
    assert len(delete_calls) == 1


async def test_cancelled_task_is_not_restarted_by_resume(client, runtime):
    from fakes import SlowTool

    runtime.registry.register(SlowTool())
    task = await execute_tool(client, "test.sleep", {"delay": 1.0})
    task_id = task["id"]
    await wait_for_state(client, task_id, {"EXECUTE", "TOOL_SELECTION"})
    await client.post(f"/api/tasks/{task_id}/cancel")
    view = await wait_for_task(client, task_id)
    assert view["state"] == "CANCELLED"
    await runtime.orchestrator.enqueue(task_id)  # simulate a reconnect retry
    await asyncio.sleep(0.2)
    after = (await client.get(f"/api/tasks/{task_id}")).json()
    assert after["state"] == "CANCELLED"
    assert len(after["tool_calls"]) == len(view["tool_calls"])
