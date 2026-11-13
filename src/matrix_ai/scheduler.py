"""Bounded background scheduler and persistent core health monitor.

Only owner-created jobs run; each tick re-checks the emergency stop, every job is
rate-limited by its own interval, and the configured global autonomy level is a
hard ceiling for each job's requested autonomy. Health transitions are persisted
so optional Telegram alerts are sent once per transition, not on every poll.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
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
        # The owner recovery preference applies to the local inbox as well as push.
        preferences = (
            self.store.notification_preferences()
            if hasattr(self.store, "notification_preferences")
            else {"health_recovered": True}
        )
        show_inbox_event = state == "degraded" or preferences.get("health_recovered", True)
        if show_inbox_event and hasattr(self.store, "create_notification"):
            urgent = state == "degraded"
            title = "MATRIX needs attention" if urgent else "MATRIX recovered"
            message = (
                f"Database: {components['database']}; Local model: {components['model']}; "
                f"Emergency stop: {components['emergency_stop']}"
            )
            self.store.create_notification(
                title=title,
                message=message,
                severity="critical" if components.get("emergency_stop") == "active" else ("warning" if urgent else "success"),
                event_type="health.degraded" if urgent else "health.recovered",
            )
        if not self._configured_notification_channels(state):
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
        pending.append({
            "event_id": uuid.uuid4().hex,
            "state": state,
            "components": components,
            "delivered_channels": [],
        })
        self.store.set_flag(
            "pending_health_notifications",
            json.dumps(pending, separators=(",", ":")),
        )

    def _configured_notification_channels(self, state: str | None = None) -> set[str]:
        preferences = (
            self.store.notification_preferences()
            if hasattr(self.store, "notification_preferences")
            else {"health_recovered": True}
        )
        if state == "healthy" and not preferences.get("health_recovered", True):
            return set()
        channels: set[str] = set()
        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            channels.add("telegram")
        if self.config.notification_webhook_url and self.config.notification_webhook_secret:
            channels.add("webhook")
        return channels

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
                "notification.health_queue_invalid", actor="health-monitor", status="discarded"
            )
            return

        while pending:
            item = pending[0]
            try:
                state = item["state"]
                components = item["components"]
                if state not in {"healthy", "degraded"} or not isinstance(components, dict):
                    raise ValueError("invalid queued health notification")
                event_id = str(item.get("event_id") or uuid.uuid4().hex)
                delivered = item.get("delivered_channels", [])
                if not isinstance(delivered, list) or any(channel not in {"telegram", "webhook"} for channel in delivered):
                    delivered = []
            except (TypeError, ValueError, KeyError):
                pending.pop(0)
                self.store.audit(
                    "notification.health_queue_invalid", actor="health-monitor", status="discarded"
                )
                continue

            required_channels = self._configured_notification_channels(state)
            # Degradation alerts (including emergency-stop state) always bypass quiet hours.
            if state == "healthy" and self._quiet_hours_active():
                break
            if not required_channels:
                pending.pop(0)
                continue

            item["event_id"] = event_id
            try:
                delivered = await self._send_health_notification(
                    state, components, event_id, set(delivered)
                )
            except Exception as exc:  # noqa: BLE001 - keep queued alerts for retry
                self.store.audit(
                    "notification.health_failed", actor="health-monitor", status="failed",
                    detail={"error": type(exc).__name__},
                )
                delivered = set(item.get("delivered_channels", []))
            item["delivered_channels"] = sorted(delivered)
            self.store.set_flag(
                "pending_health_notifications", json.dumps(pending, separators=(",", ":"))
            )
            if required_channels.issubset(delivered):
                pending.pop(0)
                self.store.set_flag(
                    "pending_health_notifications",
                    json.dumps(pending, separators=(",", ":")) if pending else "",
                )
                continue
            # Keep the queue ordered. Successfully delivered channels are remembered
            # so a later retry does not duplicate messages to those channels.
            break

        self.store.set_flag(
            "pending_health_notifications",
            json.dumps(pending, separators=(",", ":")) if pending else "",
        )

    async def _send_health_notification(
        self, state: str, components: dict[str, str], event_id: str, delivered_channels: set[str]
    ) -> set[str]:
        delivered = set(delivered_channels)
        configured_channels = self._configured_notification_channels(state)
        if "telegram" in configured_channels and "telegram" not in delivered:
            if await self._send_telegram_health_notification(state, components):
                delivered.add("telegram")
        if "webhook" in configured_channels and "webhook" not in delivered:
            if await self._send_webhook_health_notification(state, components, event_id):
                delivered.add("webhook")
        return delivered

    async def _send_telegram_health_notification(self, state: str, components: dict[str, str]) -> bool:
        token = self.config.telegram_bot_token
        chat_id = self.config.telegram_chat_id
        if not token or not chat_id:
            return False
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
                "notification.health_failed", actor="health-monitor", status="failed",
                detail={"channel": "telegram", "error": type(exc).__name__},
            )
            log.warning("Telegram health notification failed: %s", type(exc).__name__)
            return False

    async def _send_webhook_health_notification(
        self, state: str, components: dict[str, str], event_id: str
    ) -> bool:
        url = self.config.notification_webhook_url
        secret = self.config.notification_webhook_secret
        if not url or not secret:
            return False
        payload = {
            "event_id": event_id,
            "event_type": "health.degraded" if state == "degraded" else "health.recovered",
            "severity": "critical" if components.get("emergency_stop") == "active" else ("warning" if state == "degraded" else "success"),
            "title": "MATRIX needs attention" if state == "degraded" else "MATRIX has recovered",
            "components": {
                "database": components.get("database", "unknown"),
                "model": components.get("model", "unknown"),
                "emergency_stop": components.get("emergency_stop", "unknown"),
            },
            "occurred_at": datetime.now(ZoneInfo(self.config.notification_timezone)).isoformat(),
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(8.0, connect=4.0), follow_redirects=False
            ) as client:
                response = await client.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-MATRIX-Event-ID": event_id,
                        "X-MATRIX-Signature": "sha256=" + signature,
                    },
                )
                response.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001 - notification failure must not stop MATRIX
            self.store.audit(
                "notification.health_failed", actor="health-monitor", status="failed",
                detail={"channel": "webhook", "error": type(exc).__name__},
            )
            log.warning("webhook health notification failed: %s", type(exc).__name__)
            return False

    def maybe_create_daily_digest(self) -> bool:
        """Create one local daily summary after 09:00 in the configured timezone."""
        preferences = self.store.notification_preferences()
        if not preferences.get("daily_digest", True):
            return False
        local_now = datetime.now(ZoneInfo(self.config.notification_timezone))
        if local_now.hour < 9:
            return False
        digest_date = local_now.date().isoformat()
        if self.store.get_flag("notification_daily_digest_date") == digest_date:
            return False
        tasks = self.store.list_tasks(limit=200)
        counts: dict[str, int] = {}
        for task in tasks:
            state = str(task.get("state") or "UNKNOWN").upper()
            counts[state] = counts.get(state, 0) + 1
        active_states = {
            "RECEIVED", "UNDERSTAND", "POLICY_CHECK", "PLAN", "TOOL_SELECTION",
            "EXECUTE", "VERIFY", "RETRY_SAFE", "WAITING_FOR_USER", "APPROVAL_REQUIRED",
        }
        running = sum(count for state, count in counts.items() if state in active_states)
        message = (
            f"Recent tasks: {len(tasks)}; running or awaiting action: {running}; "
            f"completed: {counts.get('COMPLETE', 0)}; failed: {counts.get('FAILED', 0)}; "
            f"emergency stop: {'active' if self.store.emergency_stop else 'off'}"
        )
        notification_id = self.store.create_notification(
            title="Your MATRIX daily summary", message=message, severity="info", event_type="daily.digest"
        )
        self.store.set_flag("notification_daily_digest_date", digest_date)
        return bool(notification_id)

    async def background_loop(self, interval_seconds: float = 60.0) -> None:
        while True:
            try:
                await self.tick()
                await self.monitor_health()
                self.maybe_create_daily_digest()
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


