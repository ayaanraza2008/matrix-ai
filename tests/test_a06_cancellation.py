"""A06 - cancellation stops a running task and prevents new steps."""

from __future__ import annotations

import asyncio

import pytest
from conftest import execute_tool, wait_for_state
from fakes import SlowTool

pytestmark = pytest.mark.acceptance


async def test_cancel_running_task(client, runtime):
    runtime.registry.register(SlowTool())
    task = await execute_tool(client, "test.sleep", {"delay": 2.0})
    await wait_for_state(client, task["id"], {"EXECUTE", "TOOL_SELECTION"})

    response = await client.post(f"/api/tasks/{task['id']}/cancel")
    assert response.status_code == 200

    cancelled = await wait_for_state(client, task["id"], {"CANCELLED"}, timeout=10)
    assert cancelled["state"] == "CANCELLED"
    assert cancelled["cancel_requested"] is True
    assert "cancelled" in (cancelled["summary"] or "").lower()
    call_count = len(cancelled["tool_calls"])

    await asyncio.sleep(0.4)
    again = (await client.get(f"/api/tasks/{task['id']}")).json()
    assert again["state"] == "CANCELLED"
    assert len(again["tool_calls"]) == call_count


async def test_cancel_before_start_is_terminal(client, runtime):
    task_id = runtime.orchestrator.create_task(request="system status")
    response = await client.post(f"/api/tasks/{task_id}/cancel")
    assert response.status_code == 200
    assert response.json()["state"] == "CANCELLED"
    view = (await client.get(f"/api/tasks/{task_id}")).json()
    assert view["state"] == "CANCELLED"
    assert view["tool_calls"] == []


async def test_emergency_stop_blocks_new_tasks(client, runtime):
    response = await client.post("/api/security/emergency-stop", json={"active": True})
    assert response.status_code == 200
    assert response.json()["active"] is True

    blocked = await client.post("/api/chat", json={"message": "system status"})
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "emergency_stop_active"

    cleared = await client.post("/api/security/emergency-stop", json={"active": False})
    assert cleared.status_code == 200
    allowed = await client.post("/api/chat", json={"message": "system status"})
    assert allowed.status_code == 202
