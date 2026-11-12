from __future__ import annotations

from types import SimpleNamespace

import pytest

from matrix_ai.config import MatrixConfig
from matrix_ai.core.models import JobView
from matrix_ai.scheduler import Scheduler


class FakeStore:
    def __init__(self, job: JobView, *, emergency_stop: bool = False):
        self.job = job
        self.emergency_stop = emergency_stop
        self.runs = []
        self.flags = {}
        self.audits = []
        self.db = SimpleNamespace(integrity_check=lambda: "ok")

    def due_jobs(self):
        return [self.job]

    def mark_job_run(self, job_id: str, *, status: str, interval_seconds: int):
        self.runs.append((job_id, status, interval_seconds))

    def get_flag(self, name: str, default=None):
        return self.flags.get(name, default)

    def set_flag(self, name: str, value: str):
        self.flags[name] = value

    def audit(self, *args, **kwargs):
        self.audits.append((args, kwargs))


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


class FakeModels:
    def __init__(self, status: str = "working"):
        self.current_status = status

    async def status(self):
        return SimpleNamespace(status=self.current_status)


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


@pytest.mark.asyncio
async def test_health_monitor_persists_transitions_without_repeating_alert_events(tmp_path):
    config = MatrixConfig(home=tmp_path)
    store = FakeStore(job(1))
    models = FakeModels()
    scheduler = Scheduler(
        store=store, orchestrator=Unused(), models=models,
        memory=Unused(), config=config,
    )

    healthy = await scheduler.monitor_health()
    assert healthy["status"] == "healthy"
    assert store.flags["health_monitor_state"] == "healthy"
    assert store.audits == []

    models.current_status = "degraded"
    degraded = await scheduler.monitor_health()
    assert degraded["status"] == "degraded"
    assert store.flags["health_monitor_state"] == "degraded"
    assert len(store.audits) == 1
    assert store.audits[0][0][0] == "system.health_transition"

    await scheduler.monitor_health()
    assert len(store.audits) == 1

    models.current_status = "working"
    recovered = await scheduler.monitor_health()
    assert recovered["status"] == "healthy"
    assert store.flags["health_monitor_state"] == "healthy"
    assert len(store.audits) == 2
