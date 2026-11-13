from __future__ import annotations

from types import SimpleNamespace

import pytest

from matrix_ai.config import MatrixConfig
from matrix_ai.errors import ConfigError
from matrix_ai.scheduler import Scheduler


class FakeStore:
    def __init__(self):
        self.flags = {}
        self.audits = []
        self.db = SimpleNamespace(integrity_check=lambda: "ok")
        self.emergency_stop = False

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
    outcomes = iter([False, True, True])

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
    assert sent == ["degraded", "degraded", "healthy"]
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
