"""Private Telegram chat bridge.

The bridge uses Telegram long polling (no inbound webhook or public port). It
accepts messages only from the configured numeric chat ID and submits them
through the same policy-checked orchestrator as the dashboard.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .config import MatrixConfig
from .core.models import TERMINAL_TASK_STATES, TaskState
from .core.orchestrator import Orchestrator
from .errors import RateLimited
from .security.ratelimit import RateLimiter
from .storage.store import Store

log = logging.getLogger("matrix.telegram")


class TelegramChatBridge:
    def __init__(
        self, *, config: MatrixConfig, orchestrator: Orchestrator, store: Store,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.config = config
        self.orchestrator = orchestrator
        self.store = store
        # Share the runtime limiter so Telegram cannot bypass API task throttling.
        self.rate_limiter = rate_limiter or RateLimiter(config.rate_limit_per_minute)
        self._task: asyncio.Task | None = None
        self._offset: int | None = None
        self._client: httpx.AsyncClient | None = None
        self._report_tasks: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.config.telegram_bot_token and self.config.telegram_chat_id)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.enabled and not self.running:
            self._task = asyncio.create_task(self._run(), name="matrix-telegram")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        for report in tuple(self._report_tasks):
            report.cancel()
        if self._report_tasks:
            await asyncio.gather(*self._report_tasks, return_exceptions=True)
        self._report_tasks.clear()
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _api(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert self._client is not None
        token = self.config.telegram_bot_token
        response = await self._client.post(
            f"https://api.telegram.org/bot{token}/{method}", json=payload
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} request was rejected")
        return data

    async def _send(self, text: str) -> None:
        # Telegram message text is limited to 4096 characters.
        for start in range(0, len(text) or 1, 3900):
            await self._api("sendMessage", {
                "chat_id": self.config.telegram_chat_id,
                "text": text[start:start + 3900] or " ",
                "disable_web_page_preview": True,
            })

    def _restore_offset(self) -> None:
        """Resume polling after the last update successfully handled and persisted."""
        saved = self.store.get_flag("telegram_update_offset")
        if saved is None:
            self._offset = None
            return
        try:
            self._offset = int(saved) + 1
        except (TypeError, ValueError):
            self.store.audit(
                "telegram.offset_invalid",
                actor="telegram",
                status="degraded",
                detail={"value_type": type(saved).__name__},
            )
            self._offset = None

    async def _run(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=8.0))
        announced = False
        try:
            self._restore_offset()
            while True:
                try:
                    if not announced:
                        # Do not drain the queue: fresh owner messages may arrive while MATRIX restarts.
                        await self._send(
                            "MATRIX Telegram bridge is online. Send a message to create a task. "
                            "Use /status, /stop, or /help."
                        )
                        announced = True

                    payload: dict[str, Any] = {
                        "timeout": 25,
                        "limit": 20,
                        "allowed_updates": ["message"],
                    }
                    if self._offset is not None:
                        payload["offset"] = self._offset
                    data = await self._api("getUpdates", payload)
                    for update in data.get("result", []):
                        update_id = update.get("update_id")
                        try:
                            await self._process_update(update)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:  # noqa: BLE001 - retry this update before advancing
                            self.store.audit(
                                "telegram.update_failed", actor="telegram", status="failed",
                                detail={"error": type(exc).__name__, "update_id": update_id},
                            )
                            log.warning("Telegram update failed: %s", type(exc).__name__)
                            break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - transient network/API errors retry
                    self.store.audit(
                        "telegram.bridge_retry", actor="telegram", status="degraded",
                        detail={"error": type(exc).__name__},
                    )
                    log.warning("Telegram bridge retrying after %s", type(exc).__name__)
                    await asyncio.sleep(5)
        finally:
            if self._client:
                await self._client.aclose()
                self._client = None

    async def _process_update(self, update: dict[str, Any]) -> None:
        """Handle one update, then persist its watermark only after success."""
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            raise ValueError("Telegram update is missing a valid update_id")
        await self._handle_update(update)
        self._offset = update_id + 1
        self.store.set_flag("telegram_update_offset", str(update_id))

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = (message.get("text") or "").strip()
        allowed_id = str(self.config.telegram_chat_id or "")
        # Require a private chat, matching chat ID, and matching sender ID.
        if chat.get("type") != "private" or str(chat.get("id")) != allowed_id:
            return
        if str(sender.get("id")) != allowed_id or not text:
            return

        self.store.audit(
            "telegram.message_received", actor="telegram", status="accepted",
            detail={"message_length": min(len(text), 8000)},
        )
        command = text.split(maxsplit=1)[0].lower().split("@", 1)[0]
        if command == "/start":
            await self._send("MATRIX is connected. Send me a task in plain language.")
            return
        if command == "/help":
            await self._send(
                "Send any task as a normal message.\n/status — system health\n"
                "/stop — activate emergency stop\n"
                "Pending HIGH/CRITICAL approvals must still be confirmed in MATRIX."
            )
            return
        if command == "/status":
            health = await self.orchestrator.models.status()
            await self._send(
                f"MATRIX model: {health.status}\nEmergency stop: "
                f"{'ACTIVE' if self.store.emergency_stop else 'off'}"
            )
            return
        if command == "/stop":
            self.store.set_emergency_stop(True, actor="telegram-owner")
            cancelled = await self.orchestrator.cancel_all()
            await self._send(f"Emergency stop activated. Cancelled tasks: {cancelled}.")
            return
        if command.startswith("/"):
            await self._send("Unknown command. Send /help for available commands.")
            return

        try:
            self.rate_limiter.check(f"telegram:{allowed_id}")
        except RateLimited as exc:
            retry_after = int(exc.detail.get("retry_after_seconds", 60))
            self.store.audit(
                "telegram.rate_limited",
                actor="telegram",
                status="blocked",
                detail={"retry_after_seconds": retry_after},
            )
            await self._send(
                f"Task rate limit reached. Please try again in about {retry_after} seconds."
            )
            return

        try:
            task_id = self.orchestrator.create_task(
                request=text[:8000], user_id="owner",
                autonomy_level=self.config.autonomy_level, kind="chat",
            )
            await self.orchestrator.enqueue(task_id)
            await self._send(f"Received. MATRIX task: {task_id}")
            report = asyncio.create_task(self._report_task(task_id), name=f"telegram-task-{task_id}")
            self._report_tasks.add(report)
            report.add_done_callback(self._report_tasks.discard)
        except Exception as exc:  # noqa: BLE001
            self.store.audit(
                "telegram.task_submission_failed", actor="telegram", status="failed",
                detail={"error": type(exc).__name__},
            )
            await self._send("MATRIX could not accept that task. Check its local logs/status.")

    async def _report_task(self, task_id: str) -> None:
        # Wait for normal completion, approval, or a bounded 15-minute timeout.
        approval_notified = False
        try:
            for _ in range(180):
                await asyncio.sleep(5)
                view = self.store.task_view(task_id)
                # Notify once, but keep watching so the owner receives the result
                # after approving or denying the action in the MATRIX dashboard.
                if view.state in {TaskState.APPROVAL_REQUIRED, TaskState.WAITING_FOR_USER}:
                    if not approval_notified:
                        await self._send(
                            f"Task {task_id} needs owner approval for a protected action. "
                            "Review and approve it in the MATRIX dashboard; Telegram cannot auto-approve."
                        )
                        approval_notified = True
                    continue
                if view.state in TERMINAL_TASK_STATES:
                    result = view.summary or view.error or f"Task finished: {view.state.value}"
                    await self._send(f"MATRIX {view.state.value}:\n{result}")
                    return
            await self._send(
                f"Task {task_id} is still running. Check /status or the MATRIX dashboard for details."
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - background reports must not leak task exceptions
            self.store.audit(
                "telegram.task_report_failed",
                actor="telegram",
                task_id=task_id,
                status="failed",
                detail={"error": type(exc).__name__},
            )
            log.warning("Telegram task report failed: %s", type(exc).__name__)
