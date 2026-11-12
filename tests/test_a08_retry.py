"""A08 - safe retries with backoff; non-idempotent actions never repeat."""

from __future__ import annotations

import pytest
from conftest import execute_tool, wait_for_task
from fakes import FlakyTool, NonIdempotentFailTool

pytestmark = pytest.mark.acceptance


async def test_idempotent_tool_retries_then_succeeds(client, runtime):
    flaky = FlakyTool()
    runtime.registry.register(flaky)
    task = await execute_tool(client, "test.flaky", {"failures": 2})
    done = await wait_for_task(client, task["id"])

    assert done["state"] == "COMPLETE"
    assert flaky.attempts == 3
    assert done["retry_count"] >= 2
    assert len(done["tool_calls"]) == 3
    statuses = [call["status"] for call in done["tool_calls"]]
    assert statuses.count("succeeded") == 1

    audit = await client.get(f"/api/tasks/{task['id']}/audit")
    assert "tool.retry" in [event["event_type"] for event in audit.json()]


async def test_non_idempotent_tool_is_never_retried(client, runtime):
    tool = NonIdempotentFailTool()
    runtime.registry.register(tool)
    task = await execute_tool(client, "test.non_idempotent_fail", {"reason": "disk error"})
    done = await wait_for_task(client, task["id"])

    assert done["state"] == "FAILED"
    assert tool.attempts == 1
    assert len(done["tool_calls"]) == 1
    assert done["retry_count"] == 0


async def test_backoff_is_bounded_and_jittered(runtime):
    delays = [runtime.orchestrator._backoff(attempt) for attempt in (1, 2, 3)]
    assert all(0 < delay <= 2.0 for delay in delays)
    assert delays[1] > delays[0]
    assert len(set(delays)) == 3  # jitter prevents lock-step retries


async def test_retry_count_is_persisted(client, runtime):
    flaky = FlakyTool()
    runtime.registry.register(flaky)
    task = await execute_tool(client, "test.flaky", {"failures": 1})
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "COMPLETE"
    row = runtime.store.get_task_row(task["id"])
    assert int(row["retry_count"]) >= 1
