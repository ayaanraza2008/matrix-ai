"""A02 - local chat degrades honestly when no model is available."""

from __future__ import annotations

import pytest
from conftest import wait_for_task

pytestmark = pytest.mark.acceptance


async def test_chat_without_model_is_honest(client):
    response = await client.post("/api/chat", json={"message": "hello there, how are you today?"})
    assert response.status_code == 202
    body = response.json()
    assert body["task"]["state"] in {"RECEIVED", "UNDERSTAND"}
    task = await wait_for_task(client, body["task"]["id"])

    assert task["state"] in {"PARTIAL", "FAILED"}
    assert task["state"] != "COMPLETE"
    summary = (task["summary"] or "").lower()
    assert "language model" in summary
    assert task["tool_calls"] == []

    audit = await client.get(f"/api/tasks/{task['id']}/audit")
    assert audit.status_code == 200
    event_types = [event["event_type"] for event in audit.json()]
    assert "answer.degraded" in event_types


async def test_model_status_reports_unconfigured(client):
    response = await client.get("/api/models")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"]["status"] == "unconfigured"
    assert payload["status"]["configured"] is False
    assert payload["status"]["model"] == "none"
    hardware = payload["hardware"]
    assert hardware["concurrent_models"] == 1
    assert hardware["total_ram_mb"] > 0
    assert isinstance(hardware["profiles"], list) and hardware["profiles"]
