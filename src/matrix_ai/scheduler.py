"""Bounded background scheduler.

Only user-created jobs run; each tick re-checks the emergency stop, every job is
rate-limited by its own interval, and proactive work never exceeds the
autonomy level stored with the job.
"""

from __future__ import annotations

import asyncio
import logging

from .config import MatrixConfig
from .core.orchestrator import Orchestrator
from .memory.service import MemoryService
from .models.manager import ModelManager
from .storage.store import Store

log = logging.getLogger("matrix.scheduler")


class Scheduler:
    def __init__(
        self,
        *,
        store: Store,
        orchestrator: Orchestrator,
        models: ModelManager,
        memory: MemoryService,
        config: MatrixConfig,
    ) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.models = models
        self.memory = memory
        self.config = config
        self._task: asyncio.Task | None = None

    async def tick(self) -> list[dict]:
        results: list[dict] = []
        due = self.store.due_jobs()
        if not due:
            return results
        for job in due:
            if self.store.emergency_stop:
                self.store.mark_job_run(job.id, status="skipped_emergency_stop", interval_seconds=job.interval_seconds)
                results.append({"job_id": job.id, "status": "skipped_emergency_stop"})
                continue
            try:
                task_id = self.orchestrator.create_task(
                    request=job.prompt,
                    autonomy_level=min(int(job.autonomy_level), 1),
                    kind="job",
                )
                await self.orchestrator.enqueue(task_id)
                self.store.mark_job_run(job.id, status="queued", interval_seconds=job.interval_seconds)
                results.append({"job_id": job.id, "status": "queued", "task_id": task_id})
            except Exception as exc:  # noqa: BLE001 - one bad job must not stop the loop
                self.store.mark_job_run(job.id, status=f"error:{type(exc).__name__}", interval_seconds=job.interval_seconds)
                self.store.audit(
                    "scheduler.job_failed",
                    actor="scheduler",
                    status="failed",
                    detail={"job_id": job.id, "error": str(exc)[:300]},
                )
                results.append({"job_id": job.id, "status": "error"})
        return results

    async def background_loop(self, interval_seconds: float = 60.0) -> None:
        while True:
            try:
                await self.tick()
                await self.models.idle_check()
                self.memory.sweep_expired()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.warning("scheduler tick failed: %s", type(exc).__name__)
            await asyncio.sleep(interval_seconds)

    def start(self, interval_seconds: float = 60.0) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.background_loop(interval_seconds), name="matrix-scheduler")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown path
                pass
        self._task = None
