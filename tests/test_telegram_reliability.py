from __future__ import annotations

from types import SimpleNamespace

import pytest

from matrix_ai.telegram import TelegramChatBridge


class CursorStore:
    def __init__(self):
        self.flags = {}
        self.audits = []

    def get_system_flag(self, name, default=None):
        return self.flags.get(name, default)

    def set_system_flag(self, name, value):
        self.flags[name] = value

    def audit(self, *args, **kwargs):
        self.audits.append((args, kwargs))


def make_bridge():
    store = CursorStore()
    config = SimpleNamespace(
        rate_limit_per_minute=10,
        telegram_bot_token="test-token",
        telegram_chat_id="12345",
    )
    bridge = TelegramChatBridge(
        config=config, orchestrator=SimpleNamespace(), store=store
    )
    return bridge, store


def test_telegram_restores_last_successful_update_offset():
    bridge, store = make_bridge()
    store.flags["telegram_update_offset"] = "100"

    bridge._restore_offset()

    assert bridge._offset == 101


@pytest.mark.asyncio
async def test_telegram_persists_offset_only_after_successful_handling():
    bridge, store = make_bridge()

    async def handle(_update):
        return None

    bridge._handle_update = handle
    await bridge._process_update({"update_id": 456, "message": {}})

    assert bridge._offset == 457
    assert store.flags["telegram_update_offset"] == "456"


@pytest.mark.asyncio
async def test_telegram_failed_update_does_not_advance_offset():
    bridge, store = make_bridge()
    store.flags["telegram_update_offset"] = "100"
    bridge._restore_offset()

    async def fail(_update):
        raise RuntimeError("temporary failure")

    bridge._handle_update = fail
    with pytest.raises(RuntimeError):
        await bridge._process_update({"update_id": 101, "message": {}})

    assert bridge._offset == 101
    assert store.flags["telegram_update_offset"] == "100"


def test_replayed_telegram_task_is_idempotent_even_after_emergency_stop(runtime):
    kwargs = {
        "request": "Open calculator",
        "user_id": "owner",
        "kind": "chat",
        "idempotency_scope": "telegram.update",
        "idempotency_key": "987654",
    }

    first = runtime.orchestrator.create_task(**kwargs)
    runtime.store.set_emergency_stop(True)
    second = runtime.orchestrator.create_task(**kwargs)

    assert first == second
    rows = runtime.db.query(
        "SELECT id FROM tasks WHERE id=?",
        (first,),
    )
    assert len(rows) == 1
