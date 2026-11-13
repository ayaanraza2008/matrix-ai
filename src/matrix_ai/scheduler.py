"""Bounded background scheduler and persistent core health monitor.

Only owner-created jobs run; each tick re-checks the emergency stop, every job is
rate-limited by its own interval, and the configured global autonomy level is a
hard ceiling for each job's requested autonomy. Health transitions are persisted
so optional Telegram alerts are sent once per transition, not on every poll.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from datetime import time as datetime_time
from zoneinfo import ZoneInfo

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
        previous_components_raw = self.store.get_flag("health_monitor_components", "{}")
        try:
            previous_components = json.loads(previous_components_raw or "{}")
        except (TypeError, ValueError):
            previous_components = {}
        self.store.set_flag(
            "health_monitor_components",
            json.dumps(components, separators=(",", ":")),
        )
        emergency_stop_activated = (
            emergency_stop and previous_components.get("emergency_stop") != "active"
        )

        if current != previous:
            self.store.set_flag("health_monitor_state", current)
            # The first healthy observation establishes a baseline, not an alert-worthy
            # recovery. A first degraded observation is still actionable and is recorded.
            if previous != "unknown" or current == "degraded":
                self.store.audit(
                    "system.health_transition",
                    actor="health-monitor",
                    status=current,
                    detail={"previous": previous, "components": components},
                )
            if current == "degraded" or previous == "degraded":
                self._queue_health_notification(current, components)

        # A newly activated emergency stop is an urgent event even if another
        # degraded component had already put the overall system in that state.
        if emergency_stop_activated and current == previous:
            self.store.audit(
                "notification.emergency_stop_alert",
                actor="health-monitor",
                status="active",
            )
            self._queue_health_notification("degraded", components)

        # Retry failed delivery on later health ticks. Routine recovery messages
        # remain queued until the configured local quiet-hours window ends.
        await self._flush_pending_health_notification()
        return {"status": current, **components}

    def _quiet_hours_active(self) -> bool:
        start = self.config.notification_quiet_hours_start
        end = self.config.notification_quiet_hours_end
        if not start or not end:
            return False
        local_now = datetime.now(ZoneInfo(self.config.notification_timezone)).time()
        quiet_start = datetime_time.fromisoformat(start)
        quiet_end = datetime_time.fromisoformat(end)
        if quiet_start < quiet_end:
            return quiet_start <= local_now < quiet_end
        return local_now >= quiet_start or local_now < quiet_end

    def _queue_health_notification(self, state: str, components: dict[str, str]) -> None:
        if not self.config.telegram_bot_token or not self.config.telegram_chat_id:
            return
        raw = self.store.get_flag("pending_health_notifications", "[]")
        try:
            pending = json.loads(raw or "[]")
            if not isinstance(pending, list):
                pending = [pending] if isinstance(pending, dict) else []
        except (TypeError, ValueError):
            pending = []
        if len(pending) >= 25:
            self.store.audit(
                "notification.health_queue_full",
                actor="health-monitor",
                status="dropped",
                detail={"new_state": state},
            )
            return
        pending.append({"state": state, "components": components})
        self.store.set_flag(
            "pending_health_notifications",
            json.dumps(pending, separators=(",", ":")),
        )

    async def _flush_pending_health_notification(self) -> None:
        raw = self.store.get_flag("pending_health_notifications")
        if not raw:
            return
        try:
            pending = json.loads(raw)
            if not isinstance(pending, list):
                raise ValueError("notification queue must be a list")
        except (TypeError, ValueError):
            self.store.set_flag("pending_health_notifications", "")
            self.store.audit(
                "notification.health_queue_invalid",
                actor="health-monitor",
                status="discarded",
            )
            return

        while pending:
            item = pending[0]
            try:
                state = item["state"]
                components = item["components"]
                if state not in {"healthy", "degraded"} or not isinstance(components, dict):
                    raise ValueError("invalid queued health notification")
            except (TypeError, ValueError, KeyError):
                pending.pop(0)
                self.store.audit(
                    "notification.health_queue_invalid",
                    actor="health-monitor",
                    status="discarded",
                )
                continue
            # Degradation alerts (including emergency-stop state) always bypass quiet hours.
            if state == "healthy" and self._quiet_hours_active():
                break

            # Persist the head before attempting delivery. A failed or interrupted
            # send must never erase an urgent alert when a later state is queued.
            self.store.set_flag(
                "pending_health_notifications",
                json.dumps(pending, separators=(",", ":")),
            )
            try:
                delivered = await self._send_health_notification(state, components)
            except Exception as exc:  # noqa: BLE001 - keep queued alerts for retry
                self.store.audit(
                    "notification.health_failed",
                    actor="health-monitor",
                    status="failed",
                    detail={"error": type(exc).__name__},
                )
                delivered = False
            if not delivered:
                break
            pending.pop(0)
            self.store.set_flag(
                "pending_health_notifications",
                json.dumps(pending, separators=(",", ":")) if pending else "",
            )

        self.store.set_flag(
            "pending_health_notifications",
            json.dumps(pending, separators=(",", ":")) if pending else "",
        )

    async def _send_health_notification(self, state: str, components: dict[str, str]) -> bool:
        token = self.config.telegram_bot_token
        chat_id = self.config.telegram_chat_id
        if not token or not chat_id:
            return True

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
            return True
        except Exception as exc:  # noqa: BLE001 - notification failure must not stop MATRIX
            self.store.audit(
                "notification.health_failed",
                actor="health-monitor",
                status="failed",
                detail={"error": type(exc).__name__},
            )
            log.warning("health notification failed: %s", type(exc).__name__)
            return False

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
