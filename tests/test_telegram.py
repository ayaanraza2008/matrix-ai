from __future__ import annotations

import asyncio
from types import SimpleNamespace

from matrix_ai.config import MatrixConfig
from matrix_ai.core.models import TaskState
from matrix_ai.security.ratelimit import RateLimiter
from matrix_ai.telegram import TelegramChatBridge


class FakeStore:
    emergency_stop = False

    def __init__(self):
        self.audits = []
        self.stopped = False
        self.flags = {}
        self.idempotent_tasks = {}
        self.task_views = {}
        self.telegram_task_ids = []
        self.nonterminal = []

    def get_idempotent_task_id(self, scope, key):
        return self.idempotent_tasks.get((scope, key))

    def is_idempotent_task(self, scope, task_id):
        return scope == "telegram.update" and task_id in self.telegram_task_ids

    def list_idempotent_task_ids(self, scope, limit=20):
        return self.telegram_task_ids[:limit] if scope == "telegram.update" else []

    def nonterminal_tasks(self):
        return list(self.nonterminal)

    def get_system_flag(self, name, default=None):
        return self.flags.get(name, default)

    def set_system_flag(self, name, value):
        self.flags[name] = value

    def audit(self, *args, **kwargs):
        self.audits.append((args, kwargs))

    def set_emergency_stop(self, active, *, actor):
        self.stopped = active
        self.emergency_stop = active

    def task_view(self, task_id):
        if task_id in self.task_views:
            return self.task_views[task_id]
        return SimpleNamespace(
            state=TaskState.RECEIVED, summary=None, error=None,
            original_request="",
        )


class FakeOrchestrator:
    def __init__(self):
        self.created = []
        self.enqueued = []

    def create_task(self, **kwargs):
        self.created.append(kwargs)
        return "task-123"

    async def enqueue(self, task_id):
        self.enqueued.append(task_id)

    async def cancel(self, task_id):
        self.cancelled = getattr(self, "cancelled", [])
        self.cancelled.append(task_id)
        return SimpleNamespace(state=TaskState.CANCELLED)

    async def cancel_all(self):
        return 2


def update(chat_id=123456789, sender_id=123456789, chat_type="private", text="/help", update_id=1):
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
    assert orchestrator.created[0]["idempotency_scope"] == "telegram.update"
    assert orchestrator.created[0]["idempotency_key"] == "1"
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

    def fail_task_view(_task_id):
        raise RuntimeError("store unavailable")

    store.task_view = fail_task_view
    store.task_views["task-123"] = None
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



async def test_telegram_voice_note_is_transcribed_and_submitted_as_owner_task(tmp_path, monkeypatch):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)

    async def transcribe(_voice):
        return "check my system status"

    monkeypatch.setattr(bridge, "_transcribe_voice", transcribe)
    await bridge._handle_update({
        "update_id": 702,
        "message": {
            "chat": {"id": 123456789, "type": "private"},
            "from": {"id": 123456789},
            "voice": {"file_id": "voice-file"},
        }
    })

    assert orchestrator.created[0]["request"] == "check my system status"
    assert orchestrator.enqueued == ["task-123"]
    assert any(message.startswith("I heard:") for message in sent)


async def test_telegram_voice_download_is_bounded_and_transcribed_in_memory(tmp_path, monkeypatch):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)

    class FakeVoiceManager:
        stt_engine = object()

        def __init__(self):
            self.audio = None
            self.suffix = None

        def transcribe_bytes(self, audio, *, suffix=None):
            self.audio = audio
            self.suffix = suffix
            return {"text": "hello matrix"}

    class FakeResponse:
        headers = {"content-length": "4"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"opus"

    class FakeClient:
        def stream(self, method, url):
            assert method == "GET"
            assert "api.telegram.org/file/bot" in url
            return FakeResponse()

    voice = FakeVoiceManager()
    bridge.voice_manager = voice
    bridge._client = FakeClient()

    async def fake_api(method, payload):
        assert method == "getFile"
        assert payload == {"file_id": "voice-file"}
        return {"result": {"file_path": "voice/file.oga"}}

    monkeypatch.setattr(bridge, "_api", fake_api)
    transcript = await bridge._transcribe_voice({
        "file_id": "voice-file",
        "file_size": 4,
        "mime_type": "audio/ogg",
    })

    assert transcript == "hello matrix"
    assert voice.audio == b"opus"
    assert voice.suffix == ".ogg"
    assert any(args[0] == "telegram.voice_transcribed" for args, _kwargs in store.audits)


async def test_telegram_rejects_oversized_voice_before_download(tmp_path):
    from matrix_ai.errors import ValidationFailure
    from matrix_ai.voice.manager import MAX_AUDIO_BYTES

    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    bridge.voice_manager = SimpleNamespace(stt_engine=object())

    try:
        await bridge._transcribe_voice({
            "file_id": "voice-file",
            "file_size": MAX_AUDIO_BYTES + 1,
            "mime_type": "audio/ogg",
        })
    except ValidationFailure:
        pass
    else:
        raise AssertionError("oversized Telegram voice note should be rejected")


async def test_telegram_persists_offset_and_does_not_discard_fresh_startup_updates(tmp_path, monkeypatch):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    store.flags["telegram_update_offset"] = "54"
    store.telegram_task_ids = ["pending-task"]
    store.task_views["pending-task"] = SimpleNamespace(state=TaskState.EXECUTE)
    restored_watchers = []
    monkeypatch.setattr(bridge, "_ensure_report_task", restored_watchers.append)
    observed_offsets = []
    second_poll = asyncio.Event()

    class FakeClient:
        async def aclose(self):
            return None

    monkeypatch.setattr("matrix_ai.telegram.httpx.AsyncClient", lambda **_kwargs: FakeClient())

    async def fake_api(method, payload):
        if method in {"sendMessage", "setMyCommands"}:
            return {"ok": True}
        assert method == "getUpdates"
        if not observed_offsets:
            assert restored_watchers == ["pending-task"]
        observed_offsets.append(payload.get("offset"))
        if len(observed_offsets) == 1:
            return {"result": [{"update_id": 55, "message": {
                "chat": {"id": 123456789, "type": "private"},
                "from": {"id": 123456789},
                "text": "/help",
            }}]}
        second_poll.set()
        await asyncio.Future()

    bridge._api = fake_api

    async def must_not_drain():
        raise AssertionError("startup must not discard pending owner updates")

    bridge._discard_pending_updates = must_not_drain
    runner = asyncio.create_task(bridge._run())
    await asyncio.wait_for(second_poll.wait(), timeout=2)
    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    assert observed_offsets[0] == 55
    assert store.flags["telegram_update_offset"] == "55"



async def test_telegram_enqueue_failure_keeps_update_retryable(tmp_path, monkeypatch):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)

    async def fail_enqueue(_task_id):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(orchestrator, "enqueue", fail_enqueue)

    try:
        await bridge._process_update(update(update_id=701, text="Run a task"))
    except RuntimeError as exc:
        assert str(exc) == "queue unavailable"
    else:
        raise AssertionError("enqueue failure must propagate so the offset is not persisted")

    assert len(orchestrator.created) == 1
    assert store.flags.get("telegram_update_offset") is None
    assert any(args[0] == "telegram.task_submission_failed" for args, _ in store.audits)


async def test_telegram_replay_recovers_existing_task_before_rate_limit(tmp_path, monkeypatch):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    store.idempotent_tasks[("telegram.update", "702")] = "existing-task"
    store.task_views["existing-task"] = SimpleNamespace(state=TaskState.RECEIVED)
    bridge.rate_limiter = RateLimiter(1)
    bridge.rate_limiter.check("telegram:123456789")

    async def report(_task_id):
        return None

    monkeypatch.setattr(bridge, "_report_task", report)
    await bridge._process_update(update(update_id=702, text="Run a task"))

    assert orchestrator.created == []
    assert orchestrator.enqueued == ["existing-task"]
    assert store.flags["telegram_update_offset"] == "702"
    assert any("Recovered previously accepted" in message for message in sent)



async def test_telegram_restores_result_watchers_for_pending_tasks(tmp_path, monkeypatch):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    store.telegram_task_ids = ["pending-task", "completed-task"]
    store.task_views["pending-task"] = SimpleNamespace(state=TaskState.EXECUTE)
    store.task_views["completed-task"] = SimpleNamespace(state=TaskState.COMPLETE)
    attached = []
    monkeypatch.setattr(bridge, "_ensure_report_task", attached.append)

    restored = bridge._restore_pending_reports()

    assert restored == 1
    assert attached == ["pending-task"]
    assert any(args[0] == "telegram.report_watchers_restored" for args, _ in store.audits)


async def test_telegram_task_commands_list_inspect_and_cancel_only_telegram_tasks(tmp_path):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    store.telegram_task_ids = ["task-abcdef123456"]
    store.task_views["task-abcdef123456"] = SimpleNamespace(
        state=TaskState.WAITING_FOR_USER,
        summary="Needs owner approval",
        error=None,
        original_request="Create a test file",
    )

    await bridge._handle_update(update(text="/tasks"))
    await bridge._handle_update(update(text="/task task-abcdef123456"))
    await bridge._handle_update(update(text="/cancel task-abcdef123456"))
    await bridge._handle_update(update(text="/task not-a-telegram-task"))

    assert "Recent MATRIX Telegram tasks:" in sent[0]
    assert "Needs owner approval" in sent[1]
    assert "cannot auto-approve" in sent[1]
    assert "cancellation processed" in sent[2].lower()
    assert orchestrator.cancelled == ["task-abcdef123456"]
    assert "does not belong" in sent[3]


async def test_telegram_resume_command_clears_only_existing_emergency_stop(tmp_path):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    store.stopped = True
    store.emergency_stop = True

    await bridge._handle_update(update(text="/resume"))

    assert store.stopped is False
    assert store.emergency_stop is False
    assert sent == [
        "Emergency stop cleared. New tasks may be accepted under the configured policy."
    ]


async def test_telegram_status_reports_task_counts_and_approval_queue(tmp_path):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)

    async def model_status():
        return SimpleNamespace(status="working", provider="ollama", model="qwen-test")

    orchestrator.models = SimpleNamespace(status=model_status)
    store.nonterminal = [
        {"state": TaskState.EXECUTE.value},
        {"state": TaskState.WAITING_FOR_USER.value},
    ]

    await bridge._handle_update(update(text="/status"))

    assert "Model: working (ollama/qwen-test)" in sent[0]
    assert "Active tasks: 2" in sent[0]
    assert "Waiting for owner approval: 1" in sent[0]



async def test_telegram_registers_supported_commands_in_bot_menu(tmp_path):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    calls = []

    async def fake_api(method, payload):
        calls.append((method, payload))
        return {"ok": True}

    bridge._api = fake_api
    await bridge._register_commands()

    assert len(calls) == 1
    method, payload = calls[0]
    assert method == "setMyCommands"
    names = {item["command"] for item in payload["commands"]}
    assert names == {"start", "help", "status", "tasks", "task", "cancel", "stop", "resume"}


async def test_telegram_command_menu_failure_does_not_disable_polling(tmp_path):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)

    async def fail_api(_method, _payload):
        raise RuntimeError("temporary Telegram API error")

    bridge._api = fail_api
    await bridge._register_commands()

    assert any(args[0] == "telegram.command_menu_failed" for args, _ in store.audits)
