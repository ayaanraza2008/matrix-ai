"""Tests for independently addressable specialist worker instances."""

from __future__ import annotations

import asyncio

import pytest

from matrix_ai.core.worker_pool import AgentWorkerPool
from matrix_ai.tools.registry import build_registry


class FakeModels:
    configured = True
    model_name = "fake-local-model"


@pytest.mark.asyncio
async def test_each_profile_has_its_own_worker_state_and_can_run_in_parallel(monkeypatch):
    profiles = [
        {
            "slug": f"specialist-{index}",
            "name": f"Specialist {index}",
            "division": "engineering" if index == 0 else "security",
            "description": "Independent test worker",
            "content": "Domain guidance used only as untrusted context.",
        }
        for index in range(3)
    ]
    pool = AgentWorkerPool(FakeModels(), build_registry(), profiles=profiles, max_concurrent=2)
    active = 0
    peak = 0

    async def fake_consult(profile, request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {
            "summary": f"{profile['name']} reviewed {request}",
            "findings": ["Independent finding"],
            "risks": [],
            "suggested_tools": ["system.get_status"],
        }

    monkeypatch.setattr(pool, "_consult_agent", fake_consult)
    results = await pool.run_team("review this task", profiles)

    assert [result["worker_slug"] for result in results] == [
        "specialist-0", "specialist-1", "specialist-2"
    ]
    assert all(result["status"] == "completed" for result in results)
    assert peak == 2
    assert pool.worker_count == 3
    assert pool.status()["completed_runs"] == 3
    workers = pool.list_workers()
    assert len({worker["id"] for worker in workers}) == 3
    assert all(worker["completed_runs"] == 1 for worker in workers)
    assert all(worker["tool_authority"] == "central_policy_broker_only" for worker in workers)


@pytest.mark.asyncio
async def test_a_worker_failure_is_isolated_from_other_workers(monkeypatch):
    profiles = [
        {"slug": "good-worker", "name": "Good", "division": "testing", "description": "", "content": ""},
        {"slug": "bad-worker", "name": "Bad", "division": "testing", "description": "", "content": ""},
    ]
    pool = AgentWorkerPool(FakeModels(), build_registry(), profiles=profiles, max_concurrent=2)

    async def fake_consult(profile, _request):
        if profile["slug"] == "bad-worker":
            raise RuntimeError("provider error")
        return {"summary": "successful independent review", "findings": [], "risks": [], "suggested_tools": []}

    monkeypatch.setattr(pool, "_consult_agent", fake_consult)
    results = await pool.run_team("task", profiles)

    assert [result["status"] for result in results] == ["completed", "failed"]
    assert pool.get_worker("good-worker")["completed_runs"] == 1
    assert pool.get_worker("bad-worker")["failed_runs"] == 1


def test_worker_catalog_never_exposes_prompt_bodies():
    profiles = [
        {
            "slug": "private-profile",
            "name": "Private",
            "division": "security",
            "description": "Visible",
            "content": "DO NOT EXPOSE",
        }
    ]
    pool = AgentWorkerPool(FakeModels(), build_registry(), profiles=profiles)
    snapshot = pool.get_worker("private-profile")
    assert snapshot is not None
    assert "content" not in snapshot
    assert "DO NOT EXPOSE" not in str(snapshot)


@pytest.mark.asyncio
async def test_worker_queue_metrics_return_to_idle_when_cancelled(monkeypatch):
    profiles = [
        {"slug": "one", "name": "One", "division": "testing", "description": "", "content": ""}
    ]
    pool = AgentWorkerPool(FakeModels(), build_registry(), profiles=profiles)
    started = asyncio.Event()

    async def slow_consult(_profile, _request):
        started.set()
        await asyncio.sleep(10)
        return {"summary": "late"}

    monkeypatch.setattr(pool, "_consult_agent", slow_consult)
    task = asyncio.create_task(pool.run_team("task", profiles))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    worker = pool.get_worker("one")
    assert worker["status"] == "idle"
    assert worker["queued"] == 0


@pytest.mark.asyncio
async def test_shutdown_disables_new_worker_runs(monkeypatch):
    profiles = [
        {"slug": "one", "name": "One", "division": "testing", "description": "", "content": ""}
    ]
    pool = AgentWorkerPool(FakeModels(), build_registry(), profiles=profiles)
    called = False

    async def fake_consult(_profile, _request):
        nonlocal called
        called = True
        return {"summary": "done"}

    monkeypatch.setattr(pool, "_consult_agent", fake_consult)
    await pool.shutdown()
    assert await pool.run_team("task", profiles) == []
    assert called is False
