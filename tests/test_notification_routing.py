from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime as RealDateTime
from types import SimpleNamespace

import pytest

import matrix_ai.scheduler as scheduler_module
from matrix_ai.config import MatrixConfig
from matrix_ai.errors import ConfigError
from matrix_ai.scheduler import Scheduler


class FakeStore:
    def __init__(self):
        self.flags = {}
        self.audits = []
        self.db = SimpleNamespace(integrity_check=lambda: "ok")
        self.emergency_stop = False
        self.notifications = []
        self.task_rows = []

    def notification_preferences(self):
        raw = self.get_flag("notification_preferences", None)
        return {"health_recovered": True, "daily_digest": True} if raw is None else raw

    def create_notification(self, **item):
        preferences = self.notification_preferences()
        if item.get("event_type") == "health.recovered" and not preferences["health_recovered"]:
            return ""
        if item.get("event_type") == "daily.digest" and not preferences["daily_digest"]:
            return ""
        self.notifications.append(item)
        return "notification-" + str(len(self.notifications))

    def list_tasks(self, *, limit=200):
        return self.task_rows[:limit]

    def get_flag(self, name, default=None):
        return self.flags.get(name, default)

    def set_flag(self, name, value):
        self.flags[name] = value

    def audit(self, *args, **kwargs):
        self.audits.append((args, kwargs))


class FakeModels:
    def __init__(self, status="working"):
        self.current_status = status

    async def status(self):
        return SimpleNamespace(status=self.current_status)


class Unused:
    pass


def make_scheduler(tmp_path, *, model_status="working"):
    config = MatrixConfig(
        home=tmp_path,
        telegram_bot_token="test-token",
        telegram_chat_id="12345",
        notification_quiet_hours_start="22:00",
        notification_quiet_hours_end="07:00",
        notification_timezone="Asia/Kolkata",
    )
    store = FakeStore()
    models = FakeModels(model_status)
    scheduler = Scheduler(
        store=store, orchestrator=Unused(), models=models,
        memory=Unused(), config=config,
    )
    return scheduler, store, models


@pytest.mark.asyncio
async def test_recovery_notification_waits_until_quiet_hours_end(tmp_path, monkeypatch):
    scheduler, store, models = make_scheduler(tmp_path, model_status="degraded")
    sent = []

    async def send(state, components, event_id, delivered_channels):
        sent.append(state)
        return set(delivered_channels) | {"telegram"}

    monkeypatch.setattr(scheduler, "_send_health_notification", send)
    monkeypatch.setattr(scheduler, "_quiet_hours_active", lambda: True)

    await scheduler.monitor_health()  # First degraded observation is urgent.
    assert sent == ["degraded"]
    assert store.get_flag("pending_health_notifications") == ""

    models.current_status = "working"
    await scheduler.monitor_health()  # Recovery is routine and is deferred.
    assert sent == ["degraded"]
    assert '"state":"healthy"' in store.get_flag("pending_health_notifications")

    monkeypatch.setattr(scheduler, "_quiet_hours_active", lambda: False)
    await scheduler.monitor_health()  # Flushes even without another state transition.
    assert sent == ["degraded", "healthy"]
    assert store.get_flag("pending_health_notifications") == ""


@pytest.mark.asyncio
async def test_degraded_alert_bypasses_quiet_hours(tmp_path, monkeypatch):
    scheduler, store, models = make_scheduler(tmp_path)
    sent = []

    async def send(state, components, event_id, delivered_channels):
        sent.append(state)
        return set(delivered_channels) | {"telegram"}

    monkeypatch.setattr(scheduler, "_send_health_notification", send)
    monkeypatch.setattr(scheduler, "_quiet_hours_active", lambda: True)

    await scheduler.monitor_health()  # Establish healthy baseline.
    models.current_status = "degraded"
    await scheduler.monitor_health()

    assert sent == ["degraded"]
    assert store.get_flag("pending_health_notifications") == ""


@pytest.mark.asyncio
async def test_failed_notification_is_retried_on_later_health_tick(tmp_path, monkeypatch):
    scheduler, store, models = make_scheduler(tmp_path, model_status="degraded")
    outcomes = iter([False, True])
    attempts = []

    async def send(state, components, event_id, delivered_channels):
        attempts.append(state)
        return set(delivered_channels) | ({"telegram"} if next(outcomes) else set())

    monkeypatch.setattr(scheduler, "_send_health_notification", send)
    await scheduler.monitor_health()
    assert store.get_flag("pending_health_notifications")
    await scheduler.monitor_health()

    assert attempts == ["degraded", "degraded"]
    assert store.get_flag("pending_health_notifications") == ""


@pytest.mark.asyncio
async def test_failed_degradation_is_not_lost_when_recovery_occurs_in_quiet_hours(tmp_path, monkeypatch):
    scheduler, store, models = make_scheduler(tmp_path, model_status="degraded")
    sent = []
    outcomes = iter([False, False, True, True])

    async def send(state, components, event_id, delivered_channels):
        sent.append(state)
        return set(delivered_channels) | ({"telegram"} if next(outcomes) else set())

    monkeypatch.setattr(scheduler, "_send_health_notification", send)
    monkeypatch.setattr(scheduler, "_quiet_hours_active", lambda: True)

    await scheduler.monitor_health()  # Degraded alert fails and remains queued.
    models.current_status = "working"
    await scheduler.monitor_health()  # Recovery is appended behind the urgent alert.
    queued = store.get_flag("pending_health_notifications")
    assert '"state":"degraded"' in queued
    assert '"state":"healthy"' in queued

    monkeypatch.setattr(scheduler, "_quiet_hours_active", lambda: False)
    await scheduler.monitor_health()
    assert sent == ["degraded", "degraded", "degraded", "healthy"]
    assert store.get_flag("pending_health_notifications") == ""


@pytest.mark.asyncio
async def test_new_emergency_stop_alert_is_sent_even_if_already_degraded(tmp_path, monkeypatch):
    scheduler, store, models = make_scheduler(tmp_path, model_status="degraded")
    sent = []

    async def send(state, components, event_id, delivered_channels):
        sent.append((state, components["emergency_stop"]))
        return set(delivered_channels) | {"telegram"}

    monkeypatch.setattr(scheduler, "_send_health_notification", send)
    monkeypatch.setattr(scheduler, "_quiet_hours_active", lambda: True)

    await scheduler.monitor_health()
    assert sent == [("degraded", "off")]

    store.emergency_stop = True
    await scheduler.monitor_health()
    assert sent == [("degraded", "off"), ("degraded", "active")]
    assert any(event[0][0] == "notification.emergency_stop_alert" for event in store.audits)


def test_quiet_hours_configuration_requires_valid_pair_and_timezone(tmp_path):
    with pytest.raises(ConfigError):
        MatrixConfig(home=tmp_path / "one", notification_quiet_hours_start="22:00")
    with pytest.raises(ConfigError):
        MatrixConfig(
            home=tmp_path / "two",
            notification_quiet_hours_start="25:00",
            notification_quiet_hours_end="07:00",
        )
    with pytest.raises(ConfigError):
        MatrixConfig(
            home=tmp_path / "three",
            notification_timezone="Mars/Olympus",
        )


def test_public_settings_expose_routing_without_secrets(tmp_path):
    config = MatrixConfig(
        home=tmp_path,
        telegram_bot_token="secret-token",
        telegram_chat_id="12345",
        notification_quiet_hours_start="22:00",
        notification_quiet_hours_end="07:00",
        notification_timezone="Asia/Kolkata",
    )

    settings = config.public_dict()
    assert settings["notification_routing"] == {
        "telegram_configured": True,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
        "timezone": "Asia/Kolkata",
        "urgent_degradation_bypasses_quiet_hours": True,
        "webhook_configured": False,
    }
    assert "secret-token" not in str(settings)


@pytest.mark.asyncio
async def test_health_transition_is_saved_to_local_inbox_without_push_configuration(tmp_path, monkeypatch):
    scheduler, store, models = make_scheduler(tmp_path, model_status="working")
    scheduler.config.telegram_bot_token = None
    scheduler.config.telegram_chat_id = None
    monkeypatch.setattr(scheduler, "_quiet_hours_active", lambda: False)

    await scheduler.monitor_health()  # Establish baseline; no notification.
    models.current_status = "degraded"
    await scheduler.monitor_health()

    assert len(store.notifications) == 1
    assert store.notifications[0]["title"] == "MATRIX needs attention"
    assert store.notifications[0]["severity"] == "warning"
    assert store.notifications[0]["event_type"] == "health.degraded"


@pytest.mark.asyncio
async def test_emergency_stop_inbox_event_is_critical(tmp_path, monkeypatch):
    scheduler, store, models = make_scheduler(tmp_path, model_status="working")
    monkeypatch.setattr(scheduler, "_quiet_hours_active", lambda: True)
    await scheduler.monitor_health()
    store.emergency_stop = True
    await scheduler.monitor_health()

    assert store.notifications[-1]["severity"] == "critical"
    assert store.notifications[-1]["event_type"] == "health.degraded"
    assert "Emergency stop: active" in store.notifications[-1]["message"]


@pytest.mark.asyncio
async def test_daily_digest_is_created_once_after_local_nine_am(tmp_path, monkeypatch):
    scheduler, store, _ = make_scheduler(tmp_path)
    store.task_rows = [
        {"state": "COMPLETE"}, {"state": "FAILED"}, {"state": "EXECUTE"},
    ]
    class FixedDateTime(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            return RealDateTime(2026, 11, 13, 9, 5, tzinfo=tz)
    monkeypatch.setattr(scheduler_module, "datetime", FixedDateTime)

    assert scheduler.maybe_create_daily_digest() is True
    assert store.notifications[-1]["event_type"] == "daily.digest"
    assert "Recent tasks: 3" in store.notifications[-1]["message"]
    assert "running or awaiting action: 1" in store.notifications[-1]["message"]
    assert scheduler.maybe_create_daily_digest() is False
    assert len(store.notifications) == 1


def test_daily_digest_respects_owner_preference(tmp_path, monkeypatch):
    scheduler, store, _ = make_scheduler(tmp_path)
    store.flags["notification_preferences"] = {"health_recovered": True, "daily_digest": False}

    class FixedDateTime(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            return RealDateTime(2026, 11, 13, 10, 0, tzinfo=tz)

    monkeypatch.setattr(scheduler_module, "datetime", FixedDateTime)

    assert scheduler.maybe_create_daily_digest() is False
    assert store.notifications == []


@pytest.mark.asyncio
async def test_webhook_retry_does_not_duplicate_successful_telegram_delivery(tmp_path, monkeypatch):
    scheduler, store, models = make_scheduler(tmp_path, model_status="degraded")
    scheduler.config.notification_webhook_url = "https://hooks.example.test/matrix"
    scheduler.config.notification_webhook_secret = "webhook-signing-secret"
    telegram_calls = []
    webhook_outcomes = iter([False, True])
    webhook_calls = []

    async def telegram(state, components):
        telegram_calls.append(state)
        return True

    async def webhook(state, components, event_id):
        webhook_calls.append(event_id)
        return next(webhook_outcomes)

    monkeypatch.setattr(scheduler, "_send_telegram_health_notification", telegram)
    monkeypatch.setattr(scheduler, "_send_webhook_health_notification", webhook)
    await scheduler.monitor_health()
    queued = __import__("json").loads(store.get_flag("pending_health_notifications"))
    assert queued[0]["delivered_channels"] == ["telegram"]

    await scheduler.monitor_health()
    assert telegram_calls == ["degraded"]
    assert len(webhook_calls) == 2
    assert webhook_calls[0] == webhook_calls[1]
    assert store.get_flag("pending_health_notifications") == ""


def test_webhook_configuration_requires_https_secret_and_exact_host_allowlist(tmp_path):
    with pytest.raises(ConfigError):
        MatrixConfig(
            home=tmp_path / "insecure", notification_webhook_url="http://hooks.example.test/x",
            notification_webhook_secret="secret", notification_webhook_allowed_hosts=("hooks.example.test",),
        )
    with pytest.raises(ConfigError):
        MatrixConfig(
            home=tmp_path / "not-allowed", notification_webhook_url="https://other.example.test/x",
            notification_webhook_secret="secret", notification_webhook_allowed_hosts=("hooks.example.test",),
        )
    with pytest.raises(ConfigError):
        MatrixConfig(
            home=tmp_path / "private-ip", notification_webhook_url="https://127.0.0.1/x",
            notification_webhook_secret="secret", notification_webhook_allowed_hosts=("127.0.0.1",),
        )
    with pytest.raises(ConfigError):
        MatrixConfig(
            home=tmp_path / "localhost", notification_webhook_url="https://localhost/x",
            notification_webhook_secret="secret", notification_webhook_allowed_hosts=("localhost",),
        )


@pytest.mark.asyncio
async def test_webhook_request_is_hmac_signed_and_redirects_are_disabled(tmp_path, monkeypatch):
    scheduler, _, _ = make_scheduler(tmp_path)
    scheduler.config.notification_webhook_url = "https://hooks.example.test/matrix"
    scheduler.config.notification_webhook_secret = "webhook-signing-secret"
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            captured["client_options"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, content, headers):
            captured.update({"url": url, "content": content, "headers": headers})
            return Response()

    monkeypatch.setattr(scheduler_module.httpx, "AsyncClient", Client)
    sent = await scheduler._send_webhook_health_notification(
        "degraded", {"database": "ok", "model": "unavailable", "emergency_stop": "off"}, "event-123"
    )

    assert sent is True
    assert captured["client_options"]["follow_redirects"] is False
    assert captured["headers"]["X-MATRIX-Event-ID"] == "event-123"
    expected = hmac.new(
        b"webhook-signing-secret", captured["content"], hashlib.sha256
    ).hexdigest()
    assert captured["headers"]["X-MATRIX-Signature"] == "sha256=" + expected
    payload = json.loads(captured["content"])
    assert payload["event_id"] == "event-123"
    assert payload["event_type"] == "health.degraded"
    assert payload["components"]["model"] == "unavailable"


@pytest.mark.asyncio
async def test_recovery_preference_suppresses_routine_push_but_not_urgent_alerts(tmp_path, monkeypatch):
    scheduler, store, models = make_scheduler(tmp_path, model_status="degraded")
    store.flags["notification_preferences"] = {"health_recovered": False, "daily_digest": True}
    sent = []

    async def send(state, components, event_id, delivered_channels):
        sent.append(state)
        return set(delivered_channels) | {"telegram"}

    monkeypatch.setattr(scheduler, "_send_health_notification", send)
    monkeypatch.setattr(scheduler, "_quiet_hours_active", lambda: False)
    await scheduler.monitor_health()
    models.current_status = "working"
    await scheduler.monitor_health()

    assert sent == ["degraded"]
    assert [item["event_type"] for item in store.notifications] == ["health.degraded"]
    assert store.get_flag("pending_health_notifications") == ""


@pytest.mark.asyncio
async def test_telegram_notification_uses_real_line_breaks(tmp_path, monkeypatch):
    scheduler, _, _ = make_scheduler(tmp_path)
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, json):
            captured.update({"url": url, "json": json})
            return Response()

    monkeypatch.setattr(scheduler_module.httpx, "AsyncClient", Client)
    sent = await scheduler._send_telegram_health_notification(
        "degraded",
        {"database": "ok", "model": "unavailable", "emergency_stop": "off"},
    )

    assert sent is True
    assert captured["json"]["text"].splitlines() == [
        "MATRIX needs attention",
        "Database: ok",
        "Local model: unavailable",
        "Emergency stop: off",
    ]
