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

    def get_system_flag(self, name, default=None):
        return self.flags.get(name, default)

    def set_system_flag(self, name, value):
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


def update(chat_id=123456789, sender_id=123456789, chat_type="private", text="/help"):
    return {"message": {
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


async def test_startup_drain_advances_offset_between_full_batches(tmp_path):
    bridge, store, orchestrator, sent = await make_bridge(tmp_path)
    payloads = []

    async def fake_api(method, payload):
        assert method == "getUpdates"
        payloads.append(dict(payload))
        if len(payloads) == 1:
            return {"result": [{"update_id": i} for i in range(100)]}
        return {"result": [{"update_id": 100}]}

    bridge._api = fake_api
    await bridge._discard_pending_updates()

    assert payloads == [
        {"timeout": 0, "limit": 100},
        {"timeout": 0, "limit": 100, "offset": 100},
    ]
    assert bridge._offset == 101


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
    store.flags["telegram.update_offset"] = "55"
    observed_offsets = []
    second_poll = asyncio.Event()

    class FakeClient:
        async def aclose(self):
            return None

    monkeypatch.setattr("matrix_ai.telegram.httpx.AsyncClient", lambda **_kwargs: FakeClient())

    async def fake_api(method, payload):
        if method == "sendMessage":
            return {"ok": True}
        assert method == "getUpdates"
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
    assert store.flags["telegram.update_offset"] == "56"
