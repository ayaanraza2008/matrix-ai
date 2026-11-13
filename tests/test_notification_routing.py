from __future__ import annotations

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

    async def send(state, components):
        sent.append(state)
        return True

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

    async def send(state, components):
        sent.append(state)
        return True

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

    async def send(state, components):
        attempts.append(state)
        return next(outcomes)

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

    async def send(state, components):
        sent.append(state)
        return next(outcomes)

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

    async def send(state, components):
        sent.append((state, components["emergency_stop"]))
        return True

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

