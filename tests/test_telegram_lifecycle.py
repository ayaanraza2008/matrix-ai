"""Unit tests for Telegram lifecycle hardening, status observability, 401/409 detection, and access auditing."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from matrix_ai.config import MatrixConfig
from matrix_ai.security.ratelimit import RateLimiter
from matrix_ai.telegram import TelegramChatBridge


class FakeStore:
    def __init__(self):
        self.audits = []
        self.flags = {}
        self.emergency_stop = False

    def audit(self, event_type, **kwargs):
        self.audits.append((event_type, kwargs))

    def get_system_flag(self, name, default=None):
        return self.flags.get(name, default)

    def set_system_flag(self, name, value):
        self.flags[name] = value

    def get_idempotent_task_id(self, scope, key):
        return None

    def is_idempotent_task(self, scope, task_id):
        return False

    def list_idempotent_task_ids(self, scope, limit=20):
        return []

    def list_nonterminal_idempotent_task_ids(self, scope, limit=100):
        return []

    def nonterminal_tasks(self):
        return []

    def list_goals(self, *, owner_id, limit=50):
        return []


def make_test_bridge(*, token="valid-token", chat_id="12345"):
    config = MatrixConfig.from_env()
    config.telegram_bot_token = token
    config.telegram_chat_id = chat_id
    store = FakeStore()
    orchestrator = SimpleNamespace()
    bridge = TelegramChatBridge(
        config=config,
        orchestrator=orchestrator,
        store=store,
        rate_limiter=RateLimiter(60),
    )
    return bridge, store


def test_telegram_status_reporting_disabled():
    bridge, _ = make_test_bridge(token="", chat_id="")
    st = bridge.status()
    assert st["configured"] is False
    assert st["enabled"] is False
    assert st["running"] is False
    assert st["polling_health"] == "disabled"
    assert st["consecutive_failures"] == 0
    assert st["last_error"] is None


def test_telegram_status_reporting_stopped_when_configured():
    bridge, _ = make_test_bridge()
    st = bridge.status()
    assert st["configured"] is True
    assert st["enabled"] is True
    assert st["running"] is False
    assert st["polling_health"] == "stopped"


@pytest.mark.asyncio
async def test_telegram_unauthorized_chat_is_audited():
    bridge, store = make_test_bridge(chat_id="12345")
    # Message from wrong chat ID
    bad_update = {
        "update_id": 1,
        "message": {
            "chat": {"id": 99999, "type": "private"},
            "from": {"id": 99999},
            "text": "hello",
        },
    }
    await bridge._handle_update(bad_update)
    assert any(
        event[0] == "telegram.unauthorized_access" and event[1]["status"] == "blocked"
        for event in store.audits
    )


@pytest.mark.asyncio
async def test_telegram_group_message_is_audited_and_rejected():
    bridge, store = make_test_bridge(chat_id="12345")
    # Group message
    group_update = {
        "update_id": 2,
        "message": {
            "chat": {"id": 12345, "type": "group"},
            "from": {"id": 12345},
            "text": "/status",
        },
    }
    await bridge._handle_update(group_update)
    audit_events = [e for e in store.audits if e[0] == "telegram.unauthorized_access"]
    assert len(audit_events) == 1
    assert audit_events[0][1]["detail"]["chat_type"] == "group"


@pytest.mark.asyncio
async def test_telegram_polling_detects_401_terminal_auth_failure():
    bridge, store = make_test_bridge()
    bridge._send = AsyncMock()
    bridge._register_commands = AsyncMock()

    request = httpx.Request("POST", "https://api.telegram.org/bot-token/getUpdates")
    response_401 = httpx.Response(401, request=request)

    async def fake_api(method, payload):
        if method == "getUpdates":
            raise httpx.HTTPStatusError("Unauthorized", request=request, response=response_401)
        return {"ok": True}

    bridge._api = fake_api

    task = asyncio.create_task(bridge._run())
    await asyncio.wait_for(task, timeout=1.0)

    assert bridge.status()["polling_health"] == "auth_failed"
    assert "401" in (bridge._last_error or "")
    assert any(e[0] == "telegram.auth_failed" for e in store.audits)
    await bridge.stop()


@pytest.mark.asyncio
async def test_telegram_polling_detects_409_conflict():
    bridge, store = make_test_bridge()
    bridge._send = AsyncMock()
    bridge._register_commands = AsyncMock()

    request = httpx.Request("POST", "https://api.telegram.org/bot-token/getUpdates")
    response_409 = httpx.Response(409, request=request)

    call_count = 0

    async def fake_api(method, payload):
        nonlocal call_count
        call_count += 1
        if method == "getUpdates":
            raise httpx.HTTPStatusError("Conflict", request=request, response=response_409)
        return {"ok": True}

    bridge._api = fake_api

    task = asyncio.create_task(bridge._run())
    await asyncio.sleep(0.05)

    assert bridge.status()["polling_health"] == "conflict"
    assert bridge._consecutive_failures >= 1
    assert any(e[0] == "telegram.polling_conflict" for e in store.audits)
    task.cancel()
    await bridge.stop()


@pytest.mark.asyncio
async def test_telegram_api_status_endpoint(client):
    response = await client.get("/api/telegram/status")
    assert response.status_code == 200
    data = response.json()
    assert "configured" in data
    assert "polling_health" in data
    assert data["polling_health"] in {"healthy", "degraded", "conflict", "auth_failed", "stopped", "disabled"}


@pytest.mark.asyncio
async def test_health_view_includes_telegram_status(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert "telegram" in data["checks"]
    assert "telegram" in data
    tg = data["telegram"]
    assert isinstance(tg, dict)
    assert "polling_health" in tg
