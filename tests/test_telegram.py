from __future__ import annotations

from types import SimpleNamespace

import pytest

from matrix_ai.config import MatrixConfig
from matrix_ai.core.models import TaskState
from matrix_ai.security.ratelimit import RateLimiter
from matrix_ai.storage.db import Database
from matrix_ai.telegram import TelegramChatBridge


class FakeStore:
    emergency_stop = False

    def __init__(self):
        self.audits = []
        self.stopped = False
        self.flags = {}

    def get_flag(self, name, default=None):
        return self.flags.get(name, default)

    def set_flag(self, name, value):
        self.flags[name] = value

    def audit(self, *args, **kwargs):
        self.audits.append((args, kwargs))

    def set_emergency_stop(self, active, *, actor):
        self.stopped = active

    def task_view(self, task_id):
        raise AssertionError("task polling is tested separately")


class FakeOrchestrator:
    def __init__(self):
        self.created = []
        self.enqueued = []

    def create_task(self, **kwargs):
        self.created.append(kwargs)
        return "task-123"

    async def enqueue(self, task_id):
        self.enqueued.append(task_id)

    async def cancel_all(self):
        return 2


def update(chat_id=123456789, sender_id=123456789, chat_type="private", text="/help", update_id=123):
    return {"update_id": update_id, "message": {
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": sender_id},
        "text": text,
    }}


async def make_bridge(tmp_path):
    config = MatrixConfig(
        home=tmp_path,
        telegram_bot_token="test-token",
        telegram_chat_id="123456789",
    )
    store = FakeStore()
    orchestrator = FakeOrchestrator()
    bridge = TelegramChatBridge(config=config, orchestrator=orchestrator, store=store)
    sent = []
    async def send(message):
        sent.append(message)
    bridge._send = send
    return bridge, store, orchestrator, sent


async def test_telegram_rejects_other_chat_and_group_messages(tmp_path):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    await bridge._handle_update(update(chat_id=999, sender_id=999, text="do something"))
    await bridge._handle_update(update(chat_type="group", text="do something"))
    await bridge._handle_update(update(chat_id=123456789, sender_id=999, text="do something"))
    assert not orchestrator.created
    assert not sent


async def test_telegram_submits_private_owner_message_to_orchestrator(tmp_path, monkeypatch):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    async def report(task_id):
        sent.append(f"report:{task_id}")
    monkeypatch.setattr(bridge, "_report_task", report)
    await bridge._handle_update(update(text="Open calculator"))
    assert orchestrator.created[0]["request"] == "Open calculator"
    assert orchestrator.created[0]["user_id"] == "owner"
    assert orchestrator.created[0]["autonomy_level"] == bridge.config.autonomy_level
    assert orchestrator.created[0]["source_update_id"] == "telegram:123"
    assert orchestrator.enqueued == ["task-123"]
    assert any("Received" in message for message in sent)


async def test_telegram_stop_command_activates_emergency_stop(tmp_path):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    await bridge._handle_update(update(text="/stop"))
    assert store.stopped is True
    assert sent == ["Emergency stop activated. Cancelled tasks: 2."]


async def test_telegram_bridge_requires_both_credentials(tmp_path):
    config = MatrixConfig(home=tmp_path, telegram_bot_token="token")
    bridge = TelegramChatBridge(config=config, orchestrator=FakeOrchestrator(), store=FakeStore())
    assert bridge.enabled is False



async def test_telegram_reports_result_after_owner_approval(tmp_path, monkeypatch):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    states = iter([
        SimpleNamespace(state=TaskState.WAITING_FOR_USER),
        SimpleNamespace(state=TaskState.COMPLETE, summary="File saved and verified.", error=None),
    ])
    store.task_view = lambda task_id: next(states)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("matrix_ai.telegram.asyncio.sleep", no_wait)

    await bridge._report_task("task-123")

    assert len(sent) == 2
    assert "needs owner approval" in sent[0]
    assert "cannot auto-approve" in sent[0]
    assert "matrix complete" in sent[1].lower()
    assert "File saved and verified." in sent[1]


async def test_telegram_report_failures_are_audited(tmp_path, monkeypatch):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    async def no_wait(_seconds):
        return None
    monkeypatch.setattr("matrix_ai.telegram.asyncio.sleep", no_wait)

    await bridge._report_task("task-123")

    assert any(args[0] == "telegram.task_report_failed" for args, _kwargs in store.audits)


async def test_telegram_unknown_commands_do_not_become_tasks(tmp_path):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)

    await bridge._handle_update(update(text="/unknown"))

    assert orchestrator.created == []
    assert sent == ["Unknown command. Send /help for available commands."]


async def test_telegram_restores_persisted_update_offset_instead_of_draining_queue(tmp_path):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    store.flags["telegram_update_offset"] = "100"

    bridge._restore_offset()

    assert bridge._offset == 101
    assert store.flags["telegram_update_offset"] == "100"


async def test_telegram_task_submission_uses_shared_rate_limiter(tmp_path, monkeypatch):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    bridge.rate_limiter = RateLimiter(1)

    async def report(_task_id):
        return None

    monkeypatch.setattr(bridge, "_report_task", report)
    await bridge._handle_update(update(text="First task"))
    await bridge._handle_update(update(text="Second task"))

    assert len(orchestrator.created) == 1
    assert any("rate limit reached" in message.lower() for message in sent)
    assert any(args[0] == "telegram.rate_limited" for args, _kwargs in store.audits)


async def test_telegram_emergency_stop_is_not_blocked_by_task_rate_limit(tmp_path):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    bridge.rate_limiter = RateLimiter(1)
    bridge.rate_limiter.check("telegram:123456789")

    await bridge._handle_update(update(text="/stop"))

    assert store.stopped is True
    assert sent == ["Emergency stop activated. Cancelled tasks: 2."]


async def test_task_creation_deduplicates_replayed_telegram_update(runtime):
    kwargs = {
        "request": "Open calculator",
        "user_id": "owner",
        "kind": "chat",
        "source_update_id": "telegram:987654",
    }
    first = runtime.orchestrator.create_task(**kwargs)
    runtime.store.set_emergency_stop(True)
    second = runtime.orchestrator.create_task(**kwargs)

    assert first == second
    runtime.store.set_emergency_stop(False)
    rows = runtime.db.query(
        "SELECT id FROM tasks WHERE source_update_id=?", ("telegram:987654",)
    )
    assert len(rows) == 1


async def test_database_migrates_telegram_source_update_key(runtime):
    columns = {
        row["name"] for row in runtime.db.query("PRAGMA table_info(tasks)")
    }
    assert "source_update_id" in columns


async def test_telegram_persists_offset_only_after_update_is_handled(tmp_path):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)

    async def handle(_update):
        return None

    bridge._handle_update = handle
    await bridge._process_update(update(update_id=456, text="hello"))

    assert bridge._offset == 457
    assert store.flags["telegram_update_offset"] == "456"


async def test_telegram_failed_update_does_not_advance_durable_offset(tmp_path):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    store.flags["telegram_update_offset"] = "100"
    bridge._restore_offset()

    async def fail(_update):
        raise RuntimeError("temporary failure")

    bridge._handle_update = fail
    with pytest.raises(RuntimeError):
        await bridge._process_update(update(update_id=101, text="retry me"))

    assert bridge._offset == 101
    assert store.flags["telegram_update_offset"] == "100"

def test_database_v3_migrates_telegram_source_update_key(tmp_path):
    path = tmp_path / "legacy.db"
    legacy = Database(path)
    legacy.execute("DROP INDEX IF EXISTS idx_tasks_source_update")
    legacy.execute("ALTER TABLE tasks DROP COLUMN source_update_id")
    legacy.execute("PRAGMA user_version=3")
    legacy.close()

    migrated = Database(path)
    try:
        columns = {
            row["name"] for row in migrated.query("PRAGMA table_info(tasks)")
        }
        assert "source_update_id" in columns
        assert migrated.query_one("PRAGMA user_version")[0] == 4
    finally:
        migrated.close()
