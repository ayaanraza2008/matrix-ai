"""Windows SAPI speech output, provider selection and speech preflight tests."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from matrix_ai.errors import ValidationFailure
from matrix_ai.voice.engines import WindowsSapiTtsEngine, build_tts_engine
from matrix_ai.voice.manager import VoiceSessionManager


class FakeProcess:
    def __init__(self, *, returncode: int | None = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.input: bytes | None = None
        self.terminated = False

    async def communicate(self, input: bytes | None = None):
        self.input = input
        return b"", self.stderr

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


@pytest.mark.asyncio
async def test_windows_sapi_passes_text_as_json_stdin_not_command_text(monkeypatch):
    process = FakeProcess()
    captured: dict = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(
        "matrix_ai.voice.engines.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    engine = WindowsSapiTtsEngine("powershell.exe")
    spoken = "Hello; exit 99; $(Remove-Item *)"
    await engine.speak(spoken)

    args = captured["args"]
    kwargs = captured["kwargs"]
    assert args[0] == "powershell.exe"
    assert args[-2] == "-Command"
    assert spoken not in " ".join(str(arg) for arg in args)
    assert json.loads(process.input.decode("utf-8")) == {"text": spoken}
    assert kwargs["stdin"] == asyncio.subprocess.PIPE
    assert "shell" not in kwargs


@pytest.mark.asyncio
async def test_windows_sapi_reports_missing_system_voice_without_echoing_stderr(monkeypatch):
    process = FakeProcess(returncode=1, stderr=b"voice error: private input text")

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(
        "matrix_ai.voice.engines.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    engine = WindowsSapiTtsEngine("powershell.exe")
    with pytest.raises(RuntimeError, match="local speech voice"):
        await engine.speak("sensitive phrase")


def test_tts_factory_requires_explicit_windows_sapi_setting(monkeypatch):
    assert build_tts_engine(SimpleNamespace(tts_provider="none")) is None
    assert build_tts_engine(SimpleNamespace(tts_provider="")) is None  # piper not installed

    monkeypatch.setattr("matrix_ai.voice.engines.sys.platform", "linux")
    assert build_tts_engine(SimpleNamespace(tts_provider="windows-sapi")) is None

    monkeypatch.setattr("matrix_ai.voice.engines.sys.platform", "win32")
    monkeypatch.setattr(
        "matrix_ai.voice.engines.shutil.which", lambda name: r"C:\Windows\powershell.exe"
    )
    engine = build_tts_engine(SimpleNamespace(tts_provider="windows-sapi"))
    assert isinstance(engine, WindowsSapiTtsEngine)
    assert engine.name == "windows-sapi"

    with pytest.raises(ValueError, match="unsupported"):
        build_tts_engine(SimpleNamespace(tts_provider="amazon-polly"))


def test_voice_manager_blocks_credentials_from_being_spoken(config):
    manager = VoiceSessionManager(config)
    with pytest.raises(ValidationFailure, match="credential"):
        manager.validate_speech("Bearer abcdefghijklmnopqrstuvwxyz")
    assert manager.validate_speech("MATRIX is ready") == {"allowed": True}


class BlockingEngine:
    name = "test-engine"

    def __init__(self):
        self.started = asyncio.Event()
        self.stopped = False

    async def speak(self, text: str) -> None:
        self.started.set()
        await asyncio.Event().wait()

    def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_barge_in_stops_engine_when_engine_supports_stop(config):
    engine = BlockingEngine()
    manager = VoiceSessionManager(config, tts_engine=engine)
    manager.start()
    await manager.speak("MATRIX online")
    await engine.started.wait()
    result = manager.barge_in()
    await asyncio.sleep(0)

    assert result["interrupted"] is True
    assert engine.stopped is True
    assert manager.status().state == "listening"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_voice_session_stop_stops_active_speech(config):
    engine = BlockingEngine()
    manager = VoiceSessionManager(config, tts_engine=engine)
    manager.start()
    await manager.speak("MATRIX online")
    await engine.started.wait()
    session = manager.stop()
    await asyncio.sleep(0)

    assert session.state == "idle"
    assert engine.stopped is True
    assert manager.active_session is None
    await manager.shutdown()


@pytest.mark.asyncio
async def test_windows_sapi_timeout_terminates_child(monkeypatch):
    process = FakeProcess(returncode=None)

    async def slow_communicate(input=None):
        await asyncio.Event().wait()

    process.communicate = slow_communicate

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(
        "matrix_ai.voice.engines.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    engine = WindowsSapiTtsEngine("powershell.exe", timeout_seconds=0.01)
    with pytest.raises(RuntimeError, match="timed out"):
        await engine.speak("hello")
    assert process.terminated is True


@pytest.mark.asyncio
async def test_speech_preflight_endpoint_blocks_known_credentials(client):
    safe = await client.post("/api/voice/check-speech", json={"text": "MATRIX is ready"})
    assert safe.status_code == 200
    assert safe.json() == {"allowed": True}

    unsafe = await client.post(
        "/api/voice/check-speech", json={"text": "Bearer abcdefghijklmnopqrstuvwxyz"}
    )
    assert unsafe.status_code == 422
