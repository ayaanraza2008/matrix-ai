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
from .errors import RateLimited, ToolUnavailable, ValidationFailure
from .security.ratelimit import RateLimiter
from .storage.store import Store
from .voice.manager import MAX_AUDIO_BYTES

log = logging.getLogger("matrix.telegram")


class TelegramChatBridge:
    def __init__(
        self, *, config: MatrixConfig, orchestrator: Orchestrator, store: Store,
        rate_limiter: RateLimiter | None = None,
        voice_manager: Any | None = None,
    ) -> None:
        self.config = config
        self.orchestrator = orchestrator
        self.store = store
        self.voice_manager = voice_manager
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
        """Resume after the last successfully handled and persisted update."""
        saved = self.store.get_system_flag("telegram_update_offset")
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

    async def _process_update(self, update: dict[str, Any]) -> None:
        """Handle one update, then persist its watermark only after success."""
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            raise ValueError("Telegram update is missing a valid update_id")
        await self._handle_update(update)
        self._offset = update_id + 1
        self.store.set_system_flag("telegram_update_offset", str(update_id))

    async def _run(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=8.0))
        announced = False
        try:
            self._restore_offset()
            while True:
                try:
                    if not announced:
                        # Preserve pending owner messages across MATRIX restarts.
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
                                "telegram.update_failed",
                                actor="telegram",
                                status="failed",
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

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = (message.get("text") or "").strip()
        voice = message.get("voice")
        allowed_id = str(self.config.telegram_chat_id or "")
        # Require a private chat, matching chat ID, and matching sender ID.
        if chat.get("type") != "private" or str(chat.get("id")) != allowed_id:
            return
        if str(sender.get("id")) != allowed_id:
            return
        if not text and isinstance(voice, dict):
            try:
                text = await self._transcribe_voice(voice)
            except (ToolUnavailable, ValidationFailure) as exc:
                self.store.audit(
                    "telegram.voice_unavailable", actor="telegram", status="degraded",
                    detail={"error": type(exc).__name__},
                )
                await self._send(
                    "I couldn't transcribe that voice note. Check local speech-to-text setup "
                    "or send the request as text."
                )
                return
            if text:
                await self._send(f"I heard: {text[:1000]}")
        if not text:
            return

        self.store.audit(
            "telegram.message_received", actor="telegram", status="accepted",
            detail={"message_length": min(len(text), 8000), "voice": isinstance(voice, dict)},
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

        idempotency_key = str(update["update_id"])
        # A prior attempt may have committed the task but failed before enqueue or
        # Telegram acknowledgement. Recover it before rate limiting the replay.
        existing_task_id = self.store.get_idempotent_task_id(
            "telegram.update", idempotency_key
        )
        if existing_task_id is not None:
            existing_view = self.store.task_view(existing_task_id)
            if existing_view.state is TaskState.RECEIVED:
                await self.orchestrator.enqueue(existing_task_id)
                await self._send(
                    f"Recovered previously accepted MATRIX task: {existing_task_id}"
                )
            else:
                await self._send(
                    f"Already accepted. MATRIX task: {existing_task_id} "
                    f"({existing_view.state.value})."
                )
            # Reattach the result watcher after a MATRIX restart for any
            # non-terminal task, not only tasks that still need enqueueing.
            if existing_view.state not in TERMINAL_TASK_STATES:
                self._ensure_report_task(existing_task_id)
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
                request=text[:8000],
                user_id="owner",
                autonomy_level=self.config.autonomy_level,
                kind="chat",
                idempotency_scope="telegram.update",
                idempotency_key=idempotency_key,
            )
            # Telegram may replay an update after a crash between task creation
            # and cursor persistence. Reuse the durable task, but never re-enqueue
            # work that is already running or terminal.
            view = self.store.task_view(task_id)
            if view.state is TaskState.RECEIVED:
                await self.orchestrator.enqueue(task_id)
        except Exception as exc:  # noqa: BLE001 - keep update retryable
            self.store.audit(
                "telegram.task_submission_failed", actor="telegram", status="failed",
                detail={"error": type(exc).__name__},
            )
            raise

        # Acknowledge only after durable task creation and queue submission.
        # If Telegram send fails, replay resolves to this same task ID.
        await self._send(f"Received. MATRIX task: {task_id}")
        if view.state in TERMINAL_TASK_STATES:
            result = view.summary or view.error or f"Task finished: {view.state.value}"
            await self._send(f"MATRIX {view.state.value}:\\n{result}")
        else:
            self._ensure_report_task(task_id)

    def _ensure_report_task(self, task_id: str) -> None:
        reporter_name = f"telegram-task-{task_id}"
        if any(task.get_name() == reporter_name and not task.done() for task in self._report_tasks):
            return
        report = asyncio.create_task(self._report_task(task_id), name=reporter_name)
        self._report_tasks.add(report)
        report.add_done_callback(self._report_tasks.discard)

    async def _transcribe_voice(self, voice: dict[str, Any]) -> str:
        """Download a bounded Telegram voice note and transcribe it locally in memory."""
        file_id = voice.get("file_id")
        if not isinstance(file_id, str) or not file_id or len(file_id) > 512:
            raise ValidationFailure("Telegram voice note has an invalid file ID")
        file_size = voice.get("file_size")
        if file_size is not None and (not isinstance(file_size, int) or file_size < 0 or file_size > MAX_AUDIO_BYTES):
            raise ValidationFailure("Telegram voice note exceeds the 8 MB limit")
        manager = self.voice_manager
        if manager is None or getattr(manager, "stt_engine", None) is None:
            raise ToolUnavailable("Local speech-to-text is not configured for Telegram voice notes.")
        if self._client is None:
            raise ToolUnavailable("Telegram bridge is not connected; retry the voice note shortly.")

        info = await self._api("getFile", {"file_id": file_id})
        result = info.get("result") or {}
        file_path = result.get("file_path")
        if (
            not isinstance(file_path, str)
            or not file_path
            or len(file_path) > 1024
            or file_path.startswith("/")
            or "\\" in file_path
            or "?" in file_path
            or "#" in file_path
            or any(part in {"", ".", ".."} for part in file_path.split("/"))
        ):
            raise ValidationFailure("Telegram returned an invalid voice file path")

        suffixes = {
            "audio/ogg": ".ogg",
            "audio/oga": ".oga",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "audio/wav": ".wav",
            "audio/webm": ".webm",
        }
        suffix = suffixes.get(str(voice.get("mime_type") or "").lower(), "")
        if not suffix:
            suffix = "." + file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ".ogg"
        if suffix not in {".ogg", ".oga", ".mp3", ".m4a", ".wav", ".webm"}:
            raise ValidationFailure("Telegram voice note format is not supported")

        url = f"https://api.telegram.org/file/bot{self.config.telegram_bot_token}/{file_path}"
        audio = bytearray()
        async with self._client.stream("GET", url) as response:
            response.raise_for_status()
            declared = response.headers.get("content-length")
            if declared:
                try:
                    if int(declared) > MAX_AUDIO_BYTES:
                        raise ValidationFailure("Telegram voice note exceeds the 8 MB limit")
                except ValueError as exc:
                    raise ValidationFailure("Telegram returned an invalid voice size") from exc
            async for chunk in response.aiter_bytes():
                if len(audio) + len(chunk) > MAX_AUDIO_BYTES:
                    raise ValidationFailure("Telegram voice note exceeds the 8 MB limit")
                audio.extend(chunk)
        if not audio:
            raise ValidationFailure("Telegram voice note is empty")

        try:
            if callable(getattr(manager, "transcribe_bytes", None)):
                transcript_result = await asyncio.to_thread(
                    manager.transcribe_bytes, bytes(audio), suffix=suffix
                )
            else:
                transcript_result = await asyncio.to_thread(
                    manager.stt_engine.transcribe, bytes(audio), suffix=suffix
                )
        except Exception as exc:  # noqa: BLE001 - optional codec/model errors vary
            raise ToolUnavailable(
                f"Local speech-to-text failed ({type(exc).__name__})."
            ) from exc
        transcript = str((transcript_result or {}).get("text", "")).strip()
        if not transcript:
            raise ValidationFailure("No speech could be recognized in the Telegram voice note")
        self.store.audit(
            "telegram.voice_transcribed",
            actor="telegram",
            status="succeeded",
            detail={"audio_bytes": len(audio), "characters": min(len(transcript), 1000)},
        )
        return transcript[:4000]

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
