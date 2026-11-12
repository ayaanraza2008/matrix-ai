"""A07 - a task is never marked complete without verification evidence."""

from __future__ import annotations

import json

import pytest
from conftest import execute_tool, wait_for_task
from fakes import BrokenVerifyTool

pytestmark = pytest.mark.acceptance


async def test_unverified_tool_never_completes(client, runtime):
    runtime.registry.register(BrokenVerifyTool())
    task = await execute_tool(client, "test.broken_verify", {"value": 7})
    done = await wait_for_task(client, task["id"])

    assert done["state"] in {"PARTIAL", "FAILED"}
    assert done["state"] != "COMPLETE"
    assert "verification" in (done["error"] or "").lower()
    failed_steps = [step for step in done["steps"] if step["status"] == "failed"]
    assert failed_steps
    step_row = runtime.store.step_row(task["id"], 0)
    assert "postcondition_not_met" in step_row["evidence_json"]

    evidence = json.loads(runtime.store.get_task_row(task["id"])["evidence_json"] or "{}")
    assert evidence.get("verified") is False


async def test_verified_tool_records_evidence(client, runtime):
    task = await execute_tool(client, "system.get_status", {})
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "COMPLETE"
    evidence = json.loads(runtime.store.get_task_row(task["id"])["evidence_json"])
    assert evidence["verified"] is True
    assert evidence["confidence"] >= 0.9
    assert evidence["steps"][0]["verified"] is True


async def test_audit_includes_verifier_event(client):
    task = await execute_tool(client, "system.get_status", {})
    await wait_for_task(client, task["id"])
    audit = await client.get(f"/api/tasks/{task['id']}/audit")
    events = [event["event_type"] for event in audit.json()]
    assert "tool.verified" in events
    assert "task.finalized" in events
