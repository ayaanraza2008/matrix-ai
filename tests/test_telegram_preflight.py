"""Telegram live-acceptance preflight tests (hermetic, no real API calls)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from matrix_ai.cli import main as cli_main
from matrix_ai.errors import ToolUnavailable, ValidationFailure
from matrix_ai.telegram import telegram_preflight

TOKEN = "123456:test-preflight-token"
CHAT_ID = "111222333"


def _config() -> SimpleNamespace:
    return SimpleNamespace(telegram_bot_token=TOKEN, telegram_chat_id=CHAT_ID)


def _router(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "getMe" in url:
        return httpx.Response(200, json={"ok": True, "result": {"id": 42, "username": "matrix_owner_bot"}})
    if "getWebhookInfo" in url:
        return httpx.Response(200, json={"ok": True, "result": {"url": "", "pending_update_count": 2}})
    if "sendMessage" in url:
        assert json.loads(request.content)["chat_id"] == CHAT_ID
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    return httpx.Response(400, json={"ok": False, "description": "unknown method"})


@pytest.mark.asyncio
async def test_preflight_reports_connectivity_without_exposing_the_token():
    transport = httpx.MockTransport(_router)
    report = await telegram_preflight(_config(), transport=transport)

    assert report["preflight"] == "ok"
    assert report["bot_username"] == "matrix_owner_bot"
    assert report["pending_updates"] == 2
    assert report["webhook_url"] == ""
    assert "test_message_sent" not in report
    # The token must never appear anywhere in the report.
    assert TOKEN not in json.dumps(report)


@pytest.mark.asyncio
async def test_preflight_refuses_to_run_without_configuration():
    empty = SimpleNamespace(telegram_bot_token=None, telegram_chat_id="")
    with pytest.raises(ValidationFailure):
        await telegram_preflight(empty, transport=httpx.MockTransport(_router))


@pytest.mark.asyncio
async def test_preflight_fails_closed_when_a_webhook_is_configured():
    def webhook_router(request: httpx.Request) -> httpx.Response:
        if "getWebhookInfo" in str(request.url):
            return httpx.Response(
                200, json={"ok": True, "result": {"url": "https://example.com/hook"}}
            )
        return _router(request)

    with pytest.raises(ToolUnavailable, match="webhook"):
        await telegram_preflight(_config(), transport=httpx.MockTransport(webhook_router))


@pytest.mark.asyncio
async def test_preflight_reports_rejected_token_without_echoing_details():
    def bad_token_router(request: httpx.Request) -> httpx.Response:
        if "getMe" in str(request.url):
            return httpx.Response(200, json={"ok": False, "description": "Unauthorized secret detail"})
        return _router(request)

    with pytest.raises(ToolUnavailable, match="bot token"):
        await telegram_preflight(_config(), transport=httpx.MockTransport(bad_token_router))


@pytest.mark.asyncio
async def test_preflight_can_send_one_test_message_to_the_owner_chat():
    transport = httpx.MockTransport(_router)
    report = await telegram_preflight(_config(), send_test=True, transport=transport)
    assert report["test_message_sent"] is True


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (
            "Forbidden: bot was blocked by the user",
            "Unblock the bot in Telegram",
        ),
        (
            "Forbidden: bot can't initiate conversation with a user",
            "send /start",
        ),
        (
            "Forbidden: user is deactivated",
            "account is deactivated",
        ),
        (
            "Forbidden: bot was kicked from the supergroup chat",
            "not a member",
        ),
    ],
)
@pytest.mark.asyncio
async def test_preflight_explains_known_telegram_403_causes_without_echoing_raw_body(
    description, expected
):
    def forbidden_router(request: httpx.Request) -> httpx.Response:
        if "sendMessage" in str(request.url):
            return httpx.Response(403, json={"ok": False, "description": description})
        return _router(request)

    with pytest.raises(ToolUnavailable) as caught:
        await telegram_preflight(
            _config(),
            send_test=True,
            transport=httpx.MockTransport(forbidden_router),
        )

    message = str(caught.value)
    assert "HTTP 403" in message
    assert expected in message
    assert description not in message
    assert TOKEN not in message
    assert CHAT_ID not in message


@pytest.mark.asyncio
async def test_preflight_uses_safe_generic_hint_for_unknown_telegram_403():
    secret_detail = "Forbidden: internal diagnostic detail 12345"

    def forbidden_router(request: httpx.Request) -> httpx.Response:
        if "sendMessage" in str(request.url):
            return httpx.Response(403, json={"ok": False, "description": secret_detail})
        return _router(request)

    with pytest.raises(ToolUnavailable) as caught:
        await telegram_preflight(
            _config(),
            send_test=True,
            transport=httpx.MockTransport(forbidden_router),
        )

    assert "Telegram denied delivery to the configured chat" in str(caught.value)
    assert secret_detail not in str(caught.value)
    assert TOKEN not in str(caught.value)


@pytest.mark.asyncio
async def test_preflight_survives_network_timeout_fail_closed(monkeypatch):
    def timeout_router(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("blocked")

    with pytest.raises(ToolUnavailable, match="timeout"):
        await telegram_preflight(_config(), transport=httpx.MockTransport(timeout_router))


def test_cli_telegram_preflight_uses_configured_env(monkeypatch, capsys):
    monkeypatch.setenv("MATRIX_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("MATRIX_TELEGRAM_CHAT_ID", CHAT_ID)
    monkeypatch.setenv("MATRIX_HOME", "unused-for-this-test")
    monkeypatch.setattr(
        "matrix_ai.telegram.telegram_preflight",
        _fake_preflight,
    )
    exit_code = cli_main(["telegram-preflight"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert json.loads(out)["preflight"] == "ok"
    assert TOKEN not in out


async def _fake_preflight(config, *, send_test=False, transport=None):
    return {"preflight": "ok", "send_test": send_test}


def test_cli_telegram_preflight_fails_without_configuration(monkeypatch, capsys):
    monkeypatch.delenv("MATRIX_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MATRIX_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("MATRIX_HOME", "unused-for-this-test")
    exit_code = cli_main(["telegram-preflight"])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert json.loads(out)["preflight"] == "failed"


@pytest.mark.asyncio
async def test_preflight_succeeds_in_webhook_mode_with_active_webhook():
    from matrix_ai.telegram import (
        telegram_delete_webhook,
        telegram_get_webhook_info,
        telegram_set_webhook,
    )

    def webhook_router(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "getWebhookInfo" in url:
            return httpx.Response(
                200, json={"ok": True, "result": {"url": "https://worker.sub.workers.dev", "pending_update_count": 0}}
            )
        if "setWebhook" in url:
            return httpx.Response(200, json={"ok": True, "description": "Webhook was set"})
        if "deleteWebhook" in url:
            return httpx.Response(200, json={"ok": True, "description": "Webhook was deleted"})
        return _router(request)

    transport = httpx.MockTransport(webhook_router)
    cfg = SimpleNamespace(
        telegram_bot_token=TOKEN,
        telegram_chat_id=CHAT_ID,
        telegram_mode="webhook",
        telegram_webhook_secret="test-secret",
    )

    # 1. Preflight in webhook mode accepts existing webhook
    report = await telegram_preflight(cfg, transport=transport)
    assert report["preflight"] == "ok"
    assert report["mode"] == "webhook"
    assert report["webhook_active"] is True
    assert report["webhook_url"] == "https://worker.sub.workers.dev"

    # 2. telegram_get_webhook_info
    info = await telegram_get_webhook_info(cfg, transport=transport)
    assert info["url"] == "https://worker.sub.workers.dev"

    # 3. telegram_set_webhook
    res = await telegram_set_webhook(cfg, "https://worker.sub.workers.dev", transport=transport)
    assert res["ok"] is True
    assert res["url"] == "https://worker.sub.workers.dev"

    # 4. telegram_set_webhook rejects non-https
    with pytest.raises(ValidationFailure, match="HTTPS"):
        await telegram_set_webhook(cfg, "http://insecure.com", transport=transport)

    # 5. telegram_delete_webhook
    del_res = await telegram_delete_webhook(cfg, transport=transport)
    assert del_res["ok"] is True


def test_cli_telegram_webhook_commands(monkeypatch, capsys):
    monkeypatch.setenv("MATRIX_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("MATRIX_TELEGRAM_CHAT_ID", CHAT_ID)
    monkeypatch.setenv("MATRIX_HOME", "unused")

    async def fake_info(cfg, transport=None):
        return {"url": "https://cf.workers.dev", "pending_update_count": 0}

    async def fake_set(cfg, url, *, secret_token=None, drop_pending_updates=False, transport=None):
        return {"ok": True, "url": url}

    async def fake_delete(cfg, *, drop_pending_updates=False, transport=None):
        return {"ok": True, "description": "deleted"}

    monkeypatch.setattr("matrix_ai.telegram.telegram_get_webhook_info", fake_info)
    monkeypatch.setattr("matrix_ai.telegram.telegram_set_webhook", fake_set)
    monkeypatch.setattr("matrix_ai.telegram.telegram_delete_webhook", fake_delete)

    # status
    assert cli_main(["telegram-webhook", "status"]) == 0
    assert "https://cf.workers.dev" in capsys.readouterr().out

    # set
    assert cli_main(["telegram-webhook", "set", "--url", "https://cf.workers.dev"]) == 0
    assert "https://cf.workers.dev" in capsys.readouterr().out

    # delete
    assert cli_main(["telegram-webhook", "delete"]) == 0
    assert "deleted" in capsys.readouterr().out

