"""Bounded background scheduler and persistent core health monitor.

Only owner-created jobs run; each tick re-checks the emergency stop, every job is
rate-limited by its own interval, and the configured global autonomy level is a
hard ceiling for each job's requested autonomy. Health transitions are persisted
so optional Telegram alerts are sent once per transition, not on every poll.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

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

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

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
                    autonomy_level=min(int(job.autonomy_level), int(self.config.autonomy_level)),
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

    async def monitor_health(self) -> dict[str, str]:
        """Persist health state and notify only when the core changes state."""
        try:
            database = self.store.db.integrity_check()
        except Exception as exc:  # noqa: BLE001 - health checks must not kill the loop
            database = f"error:{type(exc).__name__}"

        try:
            model = await self.models.status()
            model_status = str(model.status)
        except Exception as exc:  # noqa: BLE001
            model_status = f"error:{type(exc).__name__}"

        emergency_stop = self.store.emergency_stop
        degraded = (
            database != "ok"
            or model_status in {"unavailable", "degraded"}
            or model_status.startswith("error:")
            or emergency_stop
        )
        current = "degraded" if degraded else "healthy"
        previous = self.store.get_flag("health_monitor_state", "unknown")
        components = {
            "database": database,
            "model": model_status,
            "emergency_stop": "active" if emergency_stop else "off",
        }

        if current != previous:
            self.store.set_flag("health_monitor_state", current)
            self.store.audit(
                "system.health_transition",
                actor="health-monitor",
                status=current,
                detail={"previous": previous, "components": components},
            )
            if current == "degraded" or previous == "degraded":
                await self._send_health_notification(current, components)

        return {"status": current, **components}

    async def _send_health_notification(self, state: str, components: dict[str, str]) -> None:
        token = self.config.telegram_bot_token
        chat_id = self.config.telegram_chat_id
        if not token or not chat_id:
            return

        headline = "MATRIX needs attention" if state == "degraded" else "MATRIX has recovered"
        lines = [
            headline,
            f"Database: {components['database']}",
            f"Local model: {components['model']}",
            f"Emergency stop: {components['emergency_stop']}",
        ]
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0)) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "\n".join(lines), "disable_web_page_preview": True},
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("ok"):
                    raise RuntimeError("Telegram did not confirm the message.")
        except Exception as exc:  # noqa: BLE001 - notification failure must not stop MATRIX
            self.store.audit(
                "notification.health_failed",
                actor="health-monitor",
                status="failed",
                detail={"error": type(exc).__name__},
            )
            log.warning("health notification failed: %s", type(exc).__name__)

    async def background_loop(self, interval_seconds: float = 60.0) -> None:
        while True:
            try:
                await self.tick()
                await self.monitor_health()
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
