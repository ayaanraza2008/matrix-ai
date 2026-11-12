"""A04 - permissions are enforced server-side, not in the UI."""

from __future__ import annotations

import pytest
from conftest import execute_tool, wait_for_task

pytestmark = pytest.mark.acceptance


async def test_missing_scope_blocks_tool(client, runtime):
    task = await execute_tool(
        client,
        "files.write_text",
        {"path": "blocked.txt", "content": "should never exist"},
        scopes=["system.read"],
    )
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "FAILED"
    assert "scope" in (done["error"] or "").lower()
    assert not (runtime.config.workspace_dir / "blocked.txt").exists()
    assert done["tool_calls"] == []


async def test_autonomy_level_blocks_medium_risk(client):
    task = await execute_tool(
        client,
        "files.write_text",
        {"path": "autonomy.txt", "content": "nope"},
        autonomy_level=0,
    )
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "FAILED"
    assert "autonomy" in (done["error"] or "").lower()


async def test_unauthenticated_tool_execution_is_rejected(anon_client):
    response = await anon_client.post(
        "/api/tools/system.get_status/execute", json={"arguments": {}}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


async def test_scope_narrowing_never_widens_permissions(client, runtime):
    task = await execute_tool(
        client,
        "system.get_status",
        {},
        scopes=["files.read"],
    )
    view = await wait_for_task(client, task["id"])
    assert view["state"] == "FAILED"
    assert "scope" in (view["error"] or "").lower()
