"""Private Telegram chat bridge.

The bridge uses Telegram long polling (no inbound webhook or public port). It
accepts messages only from the configured numeric chat ID and submits them
through the same policy-checked orchestrator as the dashboard.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import UTC, datetime
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

PREFLIGHT_TIMEOUT_SECONDS = 15.0
TELEGRAM_REPORT_RESTORE_LIMIT = 100
TELEGRAM_REPORT_RETRY_ATTEMPTS = 4


def _telegram_preflight_http_hint(status: int, method: str, response: httpx.Response) -> str:
    """Return a safe, actionable hint without echoing Telegram's raw response."""
    description = ""
    try:
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get("description"), str):
            description = payload["description"].casefold()
    except (ValueError, TypeError):
        pass

    if status == 401:
        return "Telegram rejected the bot token; verify the MATRIX_TELEGRAM_BOT_TOKEN secret."
    if status == 403 and method == "sendMessage":
        if "bot was blocked by the user" in description:
            return (
                "The target Telegram user has blocked this bot. Unblock the bot in Telegram, "
                "open its private chat, send /start, then retry the preflight."
            )
        if "bot can't initiate conversation with a user" in description or "bot cannot initiate conversation with a user" in description:
            return (
                "Telegram will not let this bot start the private conversation. Open the chat "
                "with this exact bot account, send /start, verify the numeric private chat ID, "
                "then retry."
            )
        if "user is deactivated" in description:
            return "Telegram reports that the target user account is deactivated; verify the owner chat account."
        if "bot was kicked" in description or "bot is not a member" in description:
            return "The bot is not a member of the target group/channel. Add it with the required send-message permission, or configure the intended private owner chat ID."
        if "not enough rights" in description:
            return "The bot lacks permission to send messages in the target chat. Grant send-message permission or use the intended private owner chat."
        return (
            "Telegram denied delivery to the configured chat. Verify that MATRIX_TELEGRAM_CHAT_ID "
            "belongs to the same bot's private chat, that the owner has sent /start, and that "
            "the bot is not blocked."
        )
    if status == 403:
        return "Telegram denied this bot API operation; check bot restrictions and the configured chat's permissions."
    if status == 400 and method == "sendMessage":
        return "Telegram rejected the target chat; verify MATRIX_TELEGRAM_CHAT_ID and send /start to the bot from that private chat."
    if status == 400:
        return "Telegram rejected the request; verify bot configuration and webhook state."
    if status == 429:
        return "Telegram rate-limited the request; retry after the cooldown."
    if status >= 500:
        return "Telegram service returned a server error; retry later."
    return "Check Telegram bot configuration and network policy."


async def telegram_get_webhook_info(
    config: MatrixConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Retrieve current Telegram webhook status and diagnostic information."""
    if not config.telegram_bot_token:
        raise ValidationFailure("MATRIX_TELEGRAM_BOT_TOKEN is not configured.")
    timeout = httpx.Timeout(PREFLIGHT_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(
                f"https://api.telegram.org/bot{config.telegram_bot_token}/getWebhookInfo"
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                raise ToolUnavailable("Telegram getWebhookInfo request was rejected.")
            return data.get("result", {})
    except httpx.HTTPStatusError as exc:
        raise ToolUnavailable(f"Telegram Bot API getWebhookInfo failed with HTTP {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise ToolUnavailable(f"Telegram network request failed ({type(exc).__name__}).") from exc


async def telegram_set_webhook(
    config: MatrixConfig,
    url: str,
    *,
    secret_token: str | None = None,
    drop_pending_updates: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Configure an HTTPS webhook URL on Telegram with optional secret token verification."""
    if not config.telegram_bot_token:
        raise ValidationFailure("MATRIX_TELEGRAM_BOT_TOKEN is not configured.")
    if not url or not url.startswith("https://"):
        raise ValidationFailure("Telegram webhook URL must be an HTTPS URL (e.g. https://worker.domain.workers.dev).")
    secret = secret_token or config.telegram_webhook_secret
    payload: dict[str, Any] = {
        "url": url,
        "drop_pending_updates": drop_pending_updates,
        "allowed_updates": ["message"],
    }
    if secret:
        payload["secret_token"] = secret

    timeout = httpx.Timeout(PREFLIGHT_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{config.telegram_bot_token}/setWebhook",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                description = data.get("description", "Unknown error")
                raise ToolUnavailable(f"Telegram setWebhook failed: {description}")
            return {"ok": True, "description": data.get("description", "Webhook was set"), "url": url}
    except httpx.HTTPStatusError as exc:
        raise ToolUnavailable(f"Telegram Bot API setWebhook failed with HTTP {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise ToolUnavailable(f"Telegram network request failed ({type(exc).__name__}).") from exc


async def telegram_delete_webhook(
    config: MatrixConfig,
    *,
    drop_pending_updates: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Remove active webhook from Telegram, restoring compatibility with polling."""
    if not config.telegram_bot_token:
        raise ValidationFailure("MATRIX_TELEGRAM_BOT_TOKEN is not configured.")
    payload: dict[str, Any] = {"drop_pending_updates": drop_pending_updates}
    timeout = httpx.Timeout(PREFLIGHT_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{config.telegram_bot_token}/deleteWebhook",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                description = data.get("description", "Unknown error")
                raise ToolUnavailable(f"Telegram deleteWebhook failed: {description}")
            return {"ok": True, "description": data.get("description", "Webhook was deleted")}
    except httpx.HTTPStatusError as exc:
        raise ToolUnavailable(f"Telegram Bot API deleteWebhook failed with HTTP {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise ToolUnavailable(f"Telegram network request failed ({type(exc).__name__}).") from exc


async def telegram_preflight(
    config: MatrixConfig,
    *,
    send_test: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Non-destructive live-acceptance preflight for the Telegram bridge.

    Validates that the locally configured credentials can actually reach the
    Telegram Bot API before the owner starts full live acceptance. Never logs
    or returns the bot token; the optional test message goes only to the
    configured private owner chat.
    """
    if not config.telegram_bot_token or not config.telegram_chat_id:
        raise ValidationFailure(
            "Telegram is not configured; set MATRIX_TELEGRAM_BOT_TOKEN and "
            "MATRIX_TELEGRAM_CHAT_ID in the local environment/secret store."
        )
    mode = getattr(config, "telegram_mode", "polling") or "polling"
    results: dict[str, Any] = {
        "token_configured": True,
        "chat_id_configured": True,
        "mode": mode,
    }
    timeout = httpx.Timeout(PREFLIGHT_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(
                f"https://api.telegram.org/bot{config.telegram_bot_token}/getMe"
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                raise ToolUnavailable("Telegram getMe request was rejected (check the bot token).")
            bot = data.get("result", {})
            results["bot_username"] = bot.get("username")
            results["bot_id"] = bot.get("id")
            results["bot_can_read_all_group_messages"] = bool(
                bot.get("can_read_all_group_messages")
            )

            response = await client.get(
                f"https://api.telegram.org/bot{config.telegram_bot_token}/getWebhookInfo"
            )
            response.raise_for_status()
            webhook = response.json()
            if not webhook.get("ok"):
                raise ToolUnavailable("Telegram getWebhookInfo request was rejected.")
            info = webhook.get("result", {})
            webhook_url = info.get("url") or ""
            results["webhook_url"] = webhook_url
            results["pending_updates"] = info.get("pending_update_count")

            if webhook_url:
                if mode == "polling":
                    raise ToolUnavailable(
                        f"A webhook is configured for this bot ({webhook_url}); the private-chat bridge is in "
                        "polling mode and will not receive updates. Delete the webhook first "
                        "(e.g. 'matrix telegram-webhook delete'), or switch MATRIX_TELEGRAM_MODE to 'webhook'."
                    )
                results["webhook_active"] = True
            else:
                results["webhook_active"] = False
                if mode == "webhook":
                    results["webhook_warning"] = (
                        "MATRIX_TELEGRAM_MODE is set to 'webhook', but no webhook URL is configured on Telegram. "
                        "Run 'matrix telegram-webhook set --url <URL>' to register your Cloudflare Worker."
                    )

            if send_test:
                response = await client.post(
                    f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": config.telegram_chat_id,
                        "text": "MATRIX telegram-preflight test message.",
                        "disable_web_page_preview": True,
                    },
                )
                response.raise_for_status()
                sent = response.json()
                if not sent.get("ok"):
                    raise ToolUnavailable(
                        "The test message was rejected; verify the chat id and that the "
                        "owner has started a private chat with the bot."
                    )
                results["test_message_sent"] = True
    except httpx.TimeoutException as exc:
        raise ToolUnavailable("Telegram API did not respond before the preflight timeout.") from exc
    except httpx.HTTPStatusError as exc:
        # Report only the Bot API method and HTTP status. Never include the
        # request URL (it contains the bot token) or raw response body.
        method = exc.request.url.path.rsplit("/", 1)[-1]
        status = exc.response.status_code
        hint = _telegram_preflight_http_hint(status, method, exc.response)
        raise ToolUnavailable(
            f"Telegram Bot API {method} failed with HTTP {status}. {hint}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ToolUnavailable(
            f"Telegram network request failed ({type(exc).__name__}); check runner network connectivity."
        ) from exc
    results["preflight"] = "ok"
    return results


class TelegramChatBridge:
    def __init__(
        self, *, config: MatrixConfig, orchestrator: Orchestrator, store: Store,
        rate_limiter: RateLimiter | None = None,
        voice_manager: Any | None = None,
        goal_supervisor: Any | None = None,
    ) -> None:
        self.config = config
        self.orchestrator = orchestrator
        self.store = store
        self.voice_manager = voice_manager
        self.goal_supervisor = goal_supervisor
        # Share the runtime limiter so Telegram cannot bypass API task throttling.
        self.rate_limiter = rate_limiter or RateLimiter(config.rate_limit_per_minute)
        self._task: asyncio.Task | None = None
        self._offset: int | None = None
        self._client: httpx.AsyncClient | None = None
        self._report_tasks: set[asyncio.Task] = set()
        self._last_successful_poll_at: float | None = None
        self._consecutive_failures: int = 0
        self._last_error: str | None = None
        self._polling_health: str = "stopped"

    @property
    def mode(self) -> str:
        return getattr(self.config, "telegram_mode", "polling") or "polling"

    @property
    def enabled(self) -> bool:
        return bool(
            self.config.telegram_bot_token
            and self.config.telegram_chat_id
            and self.mode != "disabled"
        )

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        """Observability status report for the Telegram bridge (never discloses secrets)."""
        if not self.enabled:
            health = "disabled"
        elif self.mode == "webhook":
            health = "webhook_active"
        elif not self.running:
            health = "stopped" if self._polling_health not in {"auth_failed", "conflict"} else self._polling_health
        else:
            health = self._polling_health

        poll_iso: str | None = None
        if self._last_successful_poll_at:
            try:
                poll_iso = datetime.fromtimestamp(self._last_successful_poll_at, UTC).isoformat()
            except Exception:
                poll_iso = None

        return {
            "configured": bool(self.config.telegram_bot_token and self.config.telegram_chat_id),
            "enabled": self.enabled,
            "mode": self.mode,
            "running": self.running,
            "polling_health": health,
            "last_successful_poll_at": poll_iso,
            "last_processed_update_id": self._offset - 1 if self._offset is not None else None,
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
            "active_watchers": len([t for t in self._report_tasks if not t.done()]),
        }

    def start(self) -> None:
        if self.enabled and self.mode == "polling" and not self.running:
            self._task = asyncio.create_task(self._run(), name="matrix-telegram")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        if self._polling_health not in {"auth_failed", "conflict"}:
            self._polling_health = "stopped"
        for report in tuple(self._report_tasks):
            report.cancel()
        if self._report_tasks:
            await asyncio.gather(*self._report_tasks, return_exceptions=True)
        self._report_tasks.clear()
        if self._client:
            await self._client.aclose()
            self._client = None

    async def flush_reports(self, *, timeout: float = 10.0) -> int:
        """Wait for active task report watchers to finish delivering reports before shutting down."""
        active = [t for t in self._report_tasks if not t.done()]
        if not active:
            return 0
        try:
            await asyncio.wait(active, timeout=timeout)
        except Exception:
            pass
        return sum(1 for t in active if t.done())

    async def sync_once(self, *, max_updates: int = 50, timeout: float = 10.0) -> int:
        """Process a bounded batch of Telegram updates once and exit (VPS-free / batch mode).

        Never claims continuous polling. Restores watermark, registers commands,
        processes pending messages, and advances the persistent offset watermark.
        """
        if not self.enabled:
            return 0
        if self.mode != "polling":
            log.info("Telegram bridge mode is '%s'; sync_once skipped getUpdates to avoid webhook conflict.", self.mode)
            return 0
        close_client = False
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout + 5.0, connect=8.0))
            close_client = True
        processed = 0
        try:
            self._restore_offset()
            self._restore_pending_reports()
            await self._register_commands()
            payload: dict[str, Any] = {
                "timeout": 0,
                "limit": min(max(max_updates, 1), 100),
                "allowed_updates": ["message"],
            }
            if self._offset is not None:
                payload["offset"] = self._offset
            data = await self._api("getUpdates", payload)
            self._last_successful_poll_at = time.time()
            self._consecutive_failures = 0
            self._last_error = None
            self._polling_health = "healthy"

            for update in data.get("result", []):
                update_id = update.get("update_id")
                try:
                    await self._process_update(update)
                    processed += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self.store.audit(
                        "telegram.update_failed",
                        actor="telegram",
                        status="failed",
                        detail={"error": type(exc).__name__, "update_id": update_id},
                    )
                    log.warning("Telegram update failed: %s", type(exc).__name__)
                    break
        finally:
            if close_client and self._client is not None:
                await self._client.aclose()
                self._client = None
        return processed

    async def ingest_update(
        self,
        update: dict[str, Any],
        *,
        secret_token: str | None = None,
    ) -> bool:
        """Ingest and execute a single webhook update delivered from Cloudflare / repository_dispatch.

        Validates webhook secret token, validates integer update_id, checks deduplication
        against the persistent watermark, executes the command or task, and updates state.
        """
        if not self.enabled:
            log.warning("Telegram bridge is disabled; cannot ingest update.")
            return False

        configured_secret = getattr(self.config, "telegram_webhook_secret", None)
        if configured_secret:
            if not secret_token or secret_token.strip() != configured_secret.strip():
                self.store.audit(
                    "telegram.webhook_secret_rejected",
                    actor="telegram",
                    status="blocked",
                    detail={"reason": "secret_token_mismatch"},
                )
                raise ValidationFailure("Telegram webhook secret token mismatch.")

        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            raise ValidationFailure("Telegram update is missing a valid integer update_id.")

        # Durable idempotency check: if this update already created a terminal task in SQLite, deduplicate
        idempotency_key = str(update_id)
        existing_task = self.store.get_idempotent_task_id("telegram.update", idempotency_key)
        if existing_task:
            task_view = self.store.task_view(existing_task)
            if task_view.state in TERMINAL_TASK_STATES:
                log.info(
                    "Telegram update %d already processed (terminal task %s); deduplicated.",
                    update_id,
                    existing_task,
                )
                return True

        saved_offset = self.store.get_system_flag("telegram_update_offset")
        if saved_offset is not None:
            try:
                if update_id <= int(saved_offset):
                    if existing_task:
                        log.info("Telegram update %d already processed (task %s); deduplicated.", update_id, existing_task)
                        return True
            except (ValueError, TypeError):
                pass

        close_client = False
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0))
            close_client = True
        try:
            self._restore_offset()
            self._restore_pending_reports()
            await self._process_update(update)
            return True
        finally:
            if close_client and self._client is not None:
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
        saved = self.store.get_system_flag("telegram_update_offset")
        try:
            current_max = int(saved) if saved is not None else -1
        except (ValueError, TypeError):
            current_max = -1
        new_max = max(current_max, update_id)
        self._offset = new_max + 1
        self.store.set_system_flag("telegram_update_offset", str(new_max))

    def _restore_pending_reports(self) -> int:
        """Reattach result watchers for recent, nonterminal Telegram-created tasks."""
        list_task_ids = getattr(self.store, "list_nonterminal_idempotent_task_ids", None)
        if not callable(list_task_ids):
            return 0
        restored = 0
        task_ids: list[str] = []
        for scope in ("telegram.update", "telegram.goal"):
            try:
                task_ids.extend(
                    list_task_ids(scope, limit=TELEGRAM_REPORT_RESTORE_LIMIT)
                )
            except Exception as exc:  # noqa: BLE001 - notification recovery must not stop polling
                self.store.audit(
                    "telegram.report_restore_failed",
                    actor="telegram",
                    status="failed",
                    detail={"scope": scope, "error": type(exc).__name__},
                )
        for task_id in dict.fromkeys(task_ids):
            try:
                view = self.store.task_view(task_id)
            except Exception as exc:  # noqa: BLE001 - one stale task must not block others
                self.store.audit(
                    "telegram.report_restore_task_failed",
                    actor="telegram",
                    task_id=task_id,
                    status="failed",
                    detail={"error": type(exc).__name__},
                )
                continue
            if view.state not in TERMINAL_TASK_STATES:
                self._ensure_report_task(task_id)
                restored += 1

        if restored:
            self.store.audit(
                "telegram.report_watchers_restored",
                actor="telegram",
                status="succeeded",
                detail={"count": restored},
            )
        return restored

    async def _register_commands(self) -> None:
        """Publish the supported owner-chat command menu in Telegram clients."""
        commands = [
            {"command": "start", "description": "Connect to MATRIX"},
            {"command": "help", "description": "Show Telegram controls"},
            {"command": "status", "description": "Show system and task status"},
            {"command": "health", "description": "Show system and model health"},
            {"command": "tasks", "description": "List recent Telegram tasks"},
            {"command": "run", "description": "Execute a natural language task"},
            {"command": "task", "description": "Inspect a task by ID"},
            {"command": "cancel", "description": "Cancel a Telegram task by ID"},
            {"command": "stop", "description": "Activate emergency stop"},
            {"command": "resume", "description": "Clear emergency stop"},
        ]
        try:
            await self._api("setMyCommands", {"commands": commands})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - command menu is useful but not required for polling
            self.store.audit(
                "telegram.command_menu_failed",
                actor="telegram",
                status="degraded",
                detail={"error": type(exc).__name__},
            )
            log.warning("Telegram command menu could not be registered: %s", type(exc).__name__)

    async def _run(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=8.0))
        announced = False
        self._polling_health = "healthy"
        self._consecutive_failures = 0
        try:
            self._restore_offset()
            self._restore_pending_reports()
            await self._register_commands()
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
                    self._last_successful_poll_at = time.time()
                    self._consecutive_failures = 0
                    self._last_error = None
                    self._polling_health = "healthy"

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
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status == 401:
                        self._polling_health = "auth_failed"
                        self._last_error = "Telegram rejected the bot token (HTTP 401 Unauthorized); verify MATRIX_TELEGRAM_BOT_TOKEN."
                        self.store.audit(
                            "telegram.auth_failed",
                            actor="telegram",
                            status="failed",
                            detail={"status_code": 401},
                        )
                        log.error("Telegram bot authentication failed (HTTP 401); stopping polling.")
                        break
                    elif status == 409:
                        self._polling_health = "conflict"
                        self._consecutive_failures += 1
                        self._last_error = "Telegram polling conflict: another bot instance or webhook is active (HTTP 409 Conflict)."
                        self.store.audit(
                            "telegram.polling_conflict",
                            actor="telegram",
                            status="degraded",
                            detail={"status_code": 409, "attempts": self._consecutive_failures},
                        )
                        log.warning("Telegram polling conflict (HTTP 409); backing off.")
                        delay = min(15.0 + self._consecutive_failures * 5.0, 60.0)
                        await asyncio.sleep(delay)
                    elif status == 429:
                        self._polling_health = "degraded"
                        self._consecutive_failures += 1
                        self._last_error = "Telegram API rate-limited the polling request (HTTP 429)."
                        self.store.audit(
                            "telegram.rate_limited",
                            actor="telegram",
                            status="degraded",
                            detail={"status_code": 429, "attempts": self._consecutive_failures},
                        )
                        delay = min(10.0 + self._consecutive_failures * 5.0, 60.0)
                        await asyncio.sleep(delay)
                    else:
                        self._polling_health = "degraded"
                        self._consecutive_failures += 1
                        self._last_error = f"Telegram Bot API returned HTTP {status}."
                        self.store.audit(
                            "telegram.bridge_retry",
                            actor="telegram",
                            status="degraded",
                            detail={"status_code": status, "attempts": self._consecutive_failures},
                        )
                        delay = min(30.0, (2.0 ** min(self._consecutive_failures, 4))) + random.uniform(0.1, 1.0)
                        await asyncio.sleep(delay)
                except Exception as exc:  # noqa: BLE001 - transient network/API errors retry
                    self._polling_health = "degraded"
                    self._consecutive_failures += 1
                    self._last_error = f"Telegram bridge error: {type(exc).__name__}."
                    self.store.audit(
                        "telegram.bridge_retry",
                        actor="telegram",
                        status="degraded",
                        detail={"error": type(exc).__name__, "attempts": self._consecutive_failures},
                    )
                    log.warning("Telegram bridge retrying after %s", type(exc).__name__)
                    delay = min(30.0, (2.0 ** min(self._consecutive_failures, 4))) + random.uniform(0.1, 1.0)
                    await asyncio.sleep(delay)
        finally:
            if self._client:
                await self._client.aclose()
                self._client = None

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = (message.get("text") or "").strip()
        voice = message.get("voice")
        allowed_id = str(self.config.telegram_chat_id or "")

        is_private = chat.get("type") == "private"
        is_chat_match = str(chat.get("id")) == allowed_id
        is_sender_match = str(sender.get("id")) == allowed_id

        # Require a private chat, matching chat ID, and matching sender ID.
        if not is_private or not is_chat_match or not is_sender_match:
            self.store.audit(
                "telegram.unauthorized_access",
                actor="telegram",
                status="blocked",
                detail={
                    "chat_type": chat.get("type"),
                    "chat_id_match": is_chat_match,
                    "sender_id_match": is_sender_match,
                },
            )
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
        args = text.split(maxsplit=1)
        command_args = args[1].strip() if len(args) > 1 else ""
        if command == "/help":
            await self._send(
                "Send any task as a normal message or use commands:\n"
                "/run <instruction> — execute an autonomous task\n"
                "/status — system health and active task counts\n"
                "/health — system health and model status\n"
                "/tasks — recent Telegram tasks\n"
                "/task <task-id> — inspect one of your Telegram tasks\n"
                "/cancel <task-id> — cancel one of your Telegram tasks\n"
                "/goal <title> | <success criteria> — start a bounded autonomous goal\n"
                "/goals — list your recent goals\n"
                "/goal-status <goal-id> — inspect a goal\n"
                "/goal-pause <goal-id> / /goal-resume <goal-id> — pause or resume a goal plan\n"
                "/goal-cancel <goal-id> — cancel a goal\n"
                "/stop — activate emergency stop\n"
                "/resume — clear emergency stop\n"
                "HIGH/CRITICAL approvals must still be confirmed in the MATRIX dashboard."
            )
            return
        if command == "/status":
            health = await self.orchestrator.models.status()
            active = self.store.nonterminal_tasks()
            goals = self.store.list_goals(owner_id="owner", limit=200)
            active_goals = sum(
                1 for goal in goals
                if goal.get("state") in {
                    "queued", "planned", "running", "paused", "cancelling", "awaiting_verification"
                }
            )
            waiting = sum(
                1 for task in active
                if task["state"] in {
                    TaskState.APPROVAL_REQUIRED.value,
                    TaskState.WAITING_FOR_USER.value,
                }
            )
            tg_health = self.status()["polling_health"]
            await self._send(
                f"MATRIX Telegram: {'running' if self.running else 'starting'} ({tg_health})\n"
                f"Model: {health.status} ({health.provider}/{health.model})\n"
                f"Active tasks: {len(active)}\n"
                f"Active goals: {active_goals}\n"
                f"Waiting for owner approval: {waiting}\n"
                f"Emergency stop: {'ACTIVE' if self.store.emergency_stop else 'off'}"
            )
            return
        if command == "/goal":
            if self.goal_supervisor is None:
                await self._send("Autonomous goal control is unavailable in this runtime.")
                return
            if not command_args:
                await self._send("Usage: /goal <title> | <success criteria>")
                return
            parts = command_args.split("|", 1)
            title = parts[0].strip()
            criteria = parts[1].strip() if len(parts) > 1 else "Complete the goal and verify the outcome."
            if not title or len(title) > 2000 or not criteria or len(criteria) > 2000:
                await self._send("Goal title and success criteria must each contain 1–2000 characters.")
                return
            # Goal creation writes directly through Store to keep the goal/task/idempotency
            # rows atomic. Enforce the same emergency-stop boundary before that write;
            # otherwise /goal could bypass Orchestrator.create_task's policy check.
            if self.store.emergency_stop:
                await self._send("Emergency stop is active. No new goal was created. Use /resume only when it is safe to accept new work.")
                return
            deadline_seconds = max(1.0, min(float(self.config.task_deadline_seconds), 86400.0))
            goal_update_key = str(update["update_id"])
            if self.store.get_idempotent_task_id("telegram.goal", goal_update_key) is None:
                try:
                    self.rate_limiter.check(f"telegram:{allowed_id}")
                except RateLimited as exc:
                    retry_after = int(exc.detail.get("retry_after_seconds", 60))
                    self.store.audit(
                        "telegram.rate_limited", actor="telegram", status="blocked",
                        detail={"retry_after_seconds": retry_after, "kind": "goal"},
                    )
                    await self._send(
                        f"Goal rate limit reached. Please try again in about {retry_after} seconds."
                    )
                    return
            goal_id, task_id = self.store.create_goal_task(
                owner_id="owner",
                title=title,
                success_criteria=criteria,
                autonomy_level=self.config.autonomy_level,
                deadline_seconds=deadline_seconds,
                idempotency_key=goal_update_key,
            )
            goal = self.store.get_goal(goal_id)
            if not goal.get("task_id"):
                self.store.attach_goal_task(goal_id, task_id)
            task_view = self.store.task_view(task_id)
            if task_view.state is TaskState.RECEIVED:
                await self.orchestrator.enqueue(task_id)
            self.store.audit(
                "telegram.goal_created", actor="telegram", task_id=task_id,
                status="accepted", detail={"goal_id": goal_id, "replayed": bool(goal.get("task_id"))},
            )
            await self._send(
                f"Autonomous goal accepted.\nGoal ID: {goal_id}\nTask ID: {task_id}\n"
                f"Deadline: {int(deadline_seconds)} seconds\nUse /goal-status {goal_id} to track it."
            )
            if task_view.state not in TERMINAL_TASK_STATES:
                self._ensure_report_task(task_id)
            return
        if command == "/goals":
            goals = self.store.list_goals(owner_id="owner", limit=5)
            if not goals:
                await self._send("No MATRIX goals have been recorded yet. Start one with /goal <title> | <success criteria>.")
                return
            lines = ["Recent MATRIX goals:"]
            for goal in goals:
                lines.append(
                    f"{goal['state']} · {goal['id']} · {str(goal['title'])[:100]}"
                )
            await self._send("\n".join(lines))
            return
        if command in {"/goal-status", "/goal-pause", "/goal-resume", "/goal-cancel"}:
            if self.goal_supervisor is None:
                await self._send("Autonomous goal control is unavailable in this runtime.")
                return
            if not command_args or len(command_args) > 128 or any(ch.isspace() for ch in command_args):
                await self._send(f"Usage: {command} <goal-id>")
                return
            try:
                goal = self.store.get_goal(command_args)
            except Exception:  # noqa: BLE001 - avoid revealing whether another owner's goal exists
                await self._send("That goal was not found for this owner.")
                return
            if goal.get("owner_id") != "owner":
                await self._send("That goal was not found for this owner.")
                return
            nodes = self.store.get_goal_graph(command_args)
            if command == "/goal-pause":
                if not nodes:
                    await self._send("Pause is available for multi-step goal plans. This goal is not a goal graph.")
                    return
                try:
                    status = await self.goal_supervisor.pause(command_args)
                except ValueError as exc:
                    await self._send(f"Goal could not be paused: {exc}")
                    return
                await self._send(f"Goal paused: {command_args}\nState: {status['state']}")
                return
            if command == "/goal-resume":
                if not nodes:
                    await self._send("Resume is available for multi-step goal plans. This goal is not a goal graph.")
                    return
                try:
                    status = await self.goal_supervisor.resume(command_args)
                except ValueError as exc:
                    await self._send(f"Goal could not be resumed: {exc}")
                    return
                await self._send(f"Goal resumed: {command_args}\nState: {status['state']}")
                return
            if command == "/goal-cancel":
                if nodes:
                    status = await self.goal_supervisor.cancel(command_args)
                    await self._send(f"Goal cancellation processed: {command_args}\nState: {status['state']}")
                    return
                task = goal.get("task")
                task_id = task.get("id") if isinstance(task, dict) else None
                if not task_id:
                    await self._send("This goal has no active task to cancel.")
                    return
                task_view = self.store.task_view(task_id)
                if task_view.state in TERMINAL_TASK_STATES:
                    await self._send(f"Goal task is already terminal ({task_view.state.value}).")
                    return
                result = await self.orchestrator.cancel(task_id)
                refreshed = self.store.get_goal(command_args)
                await self._send(
                    f"Goal cancellation processed: {command_args}\nTask state: {result.state.value}\n"
                    f"Goal state: {refreshed['state']}"
                )
                return
            task = goal.get("task") or {}
            task_state = task.get("state") if isinstance(task, dict) else None
            nodes_done = sum(1 for node in nodes if node.get("state") in {"completed", "failed", "cancelled"})
            await self._send(
                f"Goal: {goal['title']}\nID: {goal['id']}\nState: {goal['state']}\n"
                f"Success criteria: {str(goal['success_criteria'])[:700]}\n"
                f"Task state: {task_state or 'not started'}\n"
                f"Plan nodes: {nodes_done}/{len(nodes)} terminal\n"
                f"Result: {str(goal.get('result_summary') or 'No final result yet.')[:1000]}"
            )
            return
        if command == "/tasks":
            task_ids = (
                self.store.list_idempotent_task_ids("telegram.update", limit=5)
                + self.store.list_idempotent_task_ids("telegram.goal", limit=5)
            )
            task_ids = list(dict.fromkeys(task_ids))[:5]
            if not task_ids:
                await self._send("No Telegram tasks have been recorded yet.")
                return
            lines = ["Recent MATRIX Telegram tasks:"]
            for task_id in task_ids:
                view = self.store.task_view(task_id)
                state = getattr(view.state, "value", str(view.state))
                summary = (view.summary or view.error or view.original_request or "").replace("\n", " ")
                lines.append(f"{state} · {task_id} · {summary[:120]}")
            await self._send("\n".join(lines))
            return
        if command in {"/task", "/cancel"}:
            if not command_args or len(command_args) > 128 or any(ch.isspace() for ch in command_args):
                await self._send(f"Usage: {command} <task-id>")
                return
            is_telegram_task = (
                self.store.is_idempotent_task("telegram.update", command_args)
                or self.store.is_idempotent_task("telegram.goal", command_args)
            )
            if not is_telegram_task:
                await self._send("That task ID does not belong to a task submitted through this Telegram chat.")
                return
            if command == "/cancel":
                prior_view = self.store.task_view(command_args)
                prior_state = getattr(prior_view.state, "value", str(prior_view.state))
                if prior_view.state in TERMINAL_TASK_STATES:
                    await self._send(f"Task {command_args[:12]} is already terminal ({prior_state}).")
                    return
                view = await self.orchestrator.cancel(command_args)
                state = getattr(view.state, "value", str(view.state))
                await self._send(f"Task {command_args[:12]} cancellation processed. Current state: {state}.")
                return
            view = self.store.task_view(command_args)
            state = getattr(view.state, "value", str(view.state))
            summary = view.summary or view.error or "No final result yet."
            response = (
                f"MATRIX task {command_args[:12]}\nState: {state}\n"
                f"Request: {(view.original_request or '')[:300]}\n"
                f"Result: {summary[:1800]}"
            )
            if view.state in {TaskState.APPROVAL_REQUIRED, TaskState.WAITING_FOR_USER}:
                response += "\nOwner approval is required in the MATRIX dashboard; Telegram cannot auto-approve."
            await self._send(response)
            return
        if command == "/stop":
            self.store.set_emergency_stop(True, actor="telegram-owner")
            cancelled = await self.orchestrator.cancel_all()
            await self._send(f"Emergency stop activated. Cancelled tasks: {cancelled}.")
            return
        if command == "/health":
            health = await self.orchestrator.models.status()
            active_count = len(self.store.nonterminal_tasks())
            memory_svc = getattr(self.orchestrator, "memory", None)
            memory_count = (
                len(memory_svc.list(limit=1000))
                if memory_svc is not None and hasattr(memory_svc, "list")
                else 0
            )
            await self._send(
                f"MATRIX health: running={self.running}, emergency_stop={self.store.emergency_stop}\n"
                f"Model: {health.status} (provider={health.provider}, model={health.model})\n"
                f"Active tasks: {active_count}\n"
                f"Memory: {memory_count} entries"
            )
            return
        if command == "/resume":
            if not self.store.emergency_stop:
                await self._send("Emergency stop is already off.")
                return
            self.store.set_emergency_stop(False, actor="telegram-owner")
            await self._send("Emergency stop cleared. New tasks may be accepted under the configured policy.")
            return
        if command == "/run":
            if not command_args:
                await self._send(
                    "Usage: /run <instruction>\n"
                    "Execute an autonomous task with natural-language instructions.\n"
                    "Example: /run Check system health and summarize active tasks"
                )
                return
            text = command_args
        elif command.startswith("/"):
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
        prefix = "1. Command received: /run\n" if command == "/run" else "1. Command received.\n"
        await self._send(f"{prefix}2. Task queued: ID {task_id}\nReceived. MATRIX task: {task_id}")
        if view.state in TERMINAL_TASK_STATES:
            result = view.summary or view.error or f"Task finished: {view.state.value}"
            if view.state is TaskState.COMPLETE:
                await self._send(f"3. Task executed successfully:\nMATRIX {view.state.value}:\n{result}")
            elif view.state is TaskState.FAILED:
                await self._send(f"4. Task failed:\nMATRIX {view.state.value}:\n{result}")
            else:
                await self._send(f"MATRIX {view.state.value}:\n{result}")
        elif view.state in {TaskState.APPROVAL_REQUIRED, TaskState.WAITING_FOR_USER}:
            await self._send(
                f"5. Task awaiting owner authorization in MATRIX dashboard:\n"
                f"Task {task_id} needs owner approval for a protected action. "
                "Review and approve it in the MATRIX dashboard; Telegram cannot auto-approve."
            )
            self._ensure_report_task(task_id)
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

    async def _send_report(self, message: str) -> None:
        """Retry transient Telegram delivery failures for task notifications."""
        for attempt in range(TELEGRAM_REPORT_RETRY_ATTEMPTS):
            try:
                await self._send(message)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt + 1 >= TELEGRAM_REPORT_RETRY_ATTEMPTS:
                    raise
                await asyncio.sleep(min(2 ** attempt, 8))

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
                        await self._send_report(
                            f"5. Task awaiting owner authorization in MATRIX dashboard:\n"
                            f"Task {task_id} needs owner approval for a protected action. "
                            "Review and approve it in the MATRIX dashboard; Telegram cannot auto-approve."
                        )
                        approval_notified = True
                    continue
                if view.state in TERMINAL_TASK_STATES:
                    result = view.summary or view.error or f"Task finished: {view.state.value}"
                    if view.state is TaskState.COMPLETE:
                        await self._send_report(
                            f"3. Task executed successfully:\nMATRIX {view.state.value}:\n{result}"
                        )
                    elif view.state is TaskState.FAILED:
                        await self._send_report(
                            f"4. Task failed:\nMATRIX {view.state.value}:\n{result}"
                        )
                    else:
                        await self._send_report(f"MATRIX {view.state.value}:\n{result}")
                    return
            await self._send_report(
                f"Task {task_id} is still running. Use /task {task_id} or the MATRIX dashboard for details."
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


__all__ = [
    "TelegramChatBridge",
    "telegram_delete_webhook",
    "telegram_get_webhook_info",
    "telegram_preflight",
    "telegram_set_webhook",
]
