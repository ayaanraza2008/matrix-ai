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
from .storage.store import Store

log = logging.getLogger("matrix.telegram")


class TelegramChatBridge:
    def __init__(self, *, config: MatrixConfig, orchestrator: Orchestrator, store: Store) -> None:
        self.config = config
        self.orchestrator = orchestrator
        self.store = store
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

    async def _run(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=8.0))
        announced = False
        try:
            while True:
                try:
                    if not announced:
                        # Drop old updates at startup so queued commands are not replayed.
                        while True:
                            initial = await self._api("getUpdates", {"timeout": 0, "limit": 100})
                            updates = initial.get("result", [])
                            if updates:
                                self._offset = max(item["update_id"] for item in updates) + 1
                            if len(updates) < 100:
                                break
                        await self._send(
                            "MATRIX Telegram bridge is online. Send a message to create a task. "
                            "Use /status, /stop, or /help."
                        )
                        announced = True

                    data = await self._api("getUpdates", {
                        "timeout": 25, "limit": 20, "offset": self._offset,
                        "allowed_updates": ["message"],
                    })
                    for update in data.get("result", []):
                        self._offset = update["update_id"] + 1
                        try:
                            await self._handle_update(update)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:  # noqa: BLE001 - isolate malformed updates
                            self.store.audit(
                                "telegram.update_failed", actor="telegram", status="failed",
                                detail={"error": type(exc).__name__},
                            )
                            log.warning("Telegram update failed: %s", type(exc).__name__)
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
        for _ in range(180):
            await asyncio.sleep(5)
            view = self.store.task_view(task_id)
            if view.state == TaskState.APPROVAL_REQUIRED:
                await self._send(
                    f"Task {task_id} needs owner approval for a protected action. "
                    "Review and approve it in the MATRIX dashboard; Telegram cannot auto-approve."
                )
                return
            if view.state in TERMINAL_TASK_STATES:
                result = view.summary or view.error or f"Task finished: {view.state.value}"
                await self._send(f"MATRIX {view.state.value}:\n{result}")
                return
        await self._send(
            f"Task {task_id} is still running. Check /status or the MATRIX dashboard for details."
        )
