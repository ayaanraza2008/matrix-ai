"""A16 - only allowlisted commands and applications can execute."""

from __future__ import annotations

import pytest
from conftest import execute_tool, wait_for_task

pytestmark = pytest.mark.acceptance


async def test_unknown_command_id_is_blocked(client):
    task = await execute_tool(client, "shell.run_allowed", {"command_id": "rm_rf_everything"})
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "FAILED"
    assert "permission_denied" in (done["error"] or "")
    assert done["tool_calls"][0]["status"] == "failed"


async def test_free_form_command_is_rejected_by_schema(client):
    response = await client.post(
        "/api/tools/shell.run_allowed/execute",
        json={"arguments": {"command": "rm -rf /"}},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"
    assert "command" in response.text


async def test_shell_metacharacters_are_rejected(client):
    response = await client.post(
        "/api/tools/shell.run_allowed/execute",
        json={"arguments": {"command_id": "git_status; rm -rf /"}},
    )
    assert response.status_code == 422


async def test_allowlisted_command_runs_and_verifies(client):
    task = await execute_tool(client, "shell.run_allowed", {"command_id": "python_version"})
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "COMPLETE", done["error"]
    output = done["tool_calls"][-1]["output"]
    assert output["exit_code"] == 0
    combined = (output["stdout"] + output["stderr"]).lower()
    assert "python" in combined


async def test_unknown_application_is_blocked(client):
    task = await execute_tool(client, "apps.open", {"app_id": "definitely_not_allowed"})
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "FAILED"
    assert "not allowlisted" in (done["error"] or "").lower() or "allowlisted" in (
        done["tool_calls"][0]["error"] or ""
    ).lower()


async def test_command_allowlist_is_small_and_declared(client):
    policies = (await client.get("/api/security/policy")).json()
    tool_names = {tool["name"] for tool in policies["tools"]}
    assert "shell.run_allowed" in tool_names
    # There is no generic arbitrary-shell tool.
    assert not any(name in tool_names for name in ("shell.run", "shell.exec", "run_shell", "terminal.run"))
