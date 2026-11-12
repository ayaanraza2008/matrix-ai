from __future__ import annotations

import pytest

from matrix_ai.config import MatrixConfig
from matrix_ai.core.models import JobView
from matrix_ai.scheduler import Scheduler


class FakeStore:
    def __init__(self, job: JobView, *, emergency_stop: bool = False):
        self.job = job
        self.emergency_stop = emergency_stop
        self.runs = []

    def due_jobs(self):
        return [self.job]

    def mark_job_run(self, job_id: str, *, status: str, interval_seconds: int):
        self.runs.append((job_id, status, interval_seconds))

    def audit(self, *args, **kwargs):
        raise AssertionError("unexpected scheduler error audit")


class FakeOrchestrator:
    def __init__(self):
        self.task_request = None
        self.enqueued = []

    def create_task(self, **kwargs):
        self.task_request = kwargs
        return "scheduled-task"

    async def enqueue(self, task_id: str):
        self.enqueued.append(task_id)


class Unused:
    pass


def job(level: int) -> JobView:
    return JobView(
        id="job-1", name="Owner goal", prompt="Review the approved workspace",
        interval_seconds=3600, autonomy_level=level, enabled=True,
    )


@pytest.mark.asyncio
async def test_scheduler_honors_job_level_when_under_global_ceiling(tmp_path):
    config = MatrixConfig(home=tmp_path, autonomy_level=2)
    store = FakeStore(job(2))
    orchestrator = FakeOrchestrator()
    scheduler = Scheduler(
        store=store, orchestrator=orchestrator, models=Unused(),
        memory=Unused(), config=config,
    )

    result = await scheduler.tick()

    assert result == [{"job_id": "job-1", "status": "queued", "task_id": "scheduled-task"}]
    assert orchestrator.task_request["autonomy_level"] == 2
    assert orchestrator.task_request["kind"] == "job"
    assert orchestrator.enqueued == ["scheduled-task"]
    assert store.runs == [("job-1", "queued", 3600)]


@pytest.mark.asyncio
async def test_scheduler_never_exceeds_global_autonomy_ceiling(tmp_path):
    config = MatrixConfig(home=tmp_path, autonomy_level=1)
    store = FakeStore(job(2))
    orchestrator = FakeOrchestrator()
    scheduler = Scheduler(
        store=store, orchestrator=orchestrator, models=Unused(),
        memory=Unused(), config=config,
    )

    await scheduler.tick()

    assert orchestrator.task_request["autonomy_level"] == 1


@pytest.mark.asyncio
async def test_emergency_stop_blocks_scheduled_jobs(tmp_path):
    config = MatrixConfig(home=tmp_path, autonomy_level=2)
    store = FakeStore(job(2), emergency_stop=True)
    orchestrator = FakeOrchestrator()
    scheduler = Scheduler(
        store=store, orchestrator=orchestrator, models=Unused(),
        memory=Unused(), config=config,
    )

    result = await scheduler.tick()

    assert result == [{"job_id": "job-1", "status": "skipped_emergency_stop"}]
    assert orchestrator.task_request is None
    assert orchestrator.enqueued == []
