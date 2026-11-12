"""A25 - a safe multi-step task completes with trace, verification and report."""

from __future__ import annotations

import json

import pytest
from conftest import wait_for_task
from fakes import FakePlanProvider

from matrix_ai.security.redaction import contains_secret

pytestmark = pytest.mark.acceptance

PLAN = {
    "goal": "Run a maintenance check and record it",
    "steps": [
        {
            "title": "Read system telemetry",
            "kind": "tool",
            "tool_name": "system.get_status",
            "arguments": {},
        },
        {
            "title": "Record the check",
            "kind": "tool",
            "tool_name": "memory.save",
            "arguments": {
                "title": "Maintenance check",
                "content": "System telemetry was read successfully during the maintenance routine.",
                "kind": "summary",
                "confidence": 0.9,
            },
        },
    ],
}


async def test_multi_step_task_is_traced_and_verified(client, runtime):
    runtime.config.model_provider = "openai"
    provider = FakePlanProvider(runtime.config)
    provider.plan = PLAN
    runtime.models.provider = provider

    response = await client.post("/api/chat", json={"message": "carry out the maintenance routine"})
    assert response.status_code == 202
    task_id = response.json()["task"]["id"]
    done = await wait_for_task(client, task_id, timeout=20)

    assert done["state"] == "COMPLETE", done["error"]
    assert len(done["steps"]) == 2
    assert all(step["status"] == "completed" for step in done["steps"])
    assert [call["tool_name"] for call in done["tool_calls"]] == [
        "system.get_status",
        "memory.save",
    ]
    assert all(call["status"] == "succeeded" for call in done["tool_calls"])

    summary = done["summary"] or ""
    assert "CPU" in summary
    assert "Stored memory" in summary

    evidence = json.loads(runtime.store.get_task_row(task_id)["evidence_json"])
    assert evidence["verified"] is True
    assert evidence["confidence"] >= 0.9

    audit = await client.get(f"/api/tasks/{task_id}/audit", params={"limit": 200})
    event_types = [event["event_type"] for event in audit.json()]
    for expected in ("task.created", "task.policy", "tool.executed", "tool.verified", "task.finalized"):
        assert expected in event_types
    assert not contains_secret(json.dumps(audit.json()))

    memory = await client.get("/api/memory/search", params={"q": "maintenance routine"})
    assert memory.json()["count"] >= 1
