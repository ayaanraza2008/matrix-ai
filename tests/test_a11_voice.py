"""A11/A12 - voice mute and barge-in controls."""

from __future__ import annotations

import asyncio
import base64
import sys
from types import ModuleType, SimpleNamespace

import pytest

from matrix_ai.errors import ValidationFailure
from matrix_ai.voice.manager import VoiceSessionManager

pytestmark = pytest.mark.acceptance

SAMPLE_AUDIO = "aGVsbG8gd29ybGQ="  # "hello world" base64


class FakeTtsEngine:
    name = "fake-tts"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.spoken: list[str] = []

    async def speak(self, text: str) -> None:
        self.spoken.append(text)
        self.started.set()
        await self.release.wait()


async def test_muted_microphone_refuses_capture(client):
    started = await client.post("/api/voice/start", json={"muted": False})
    assert started.status_code == 200
    assert started.json()["state"] == "listening"

    muted = await client.post("/api/voice/mute")
    assert muted.status_code == 200
    assert muted.json()["muted"] is True
    assert muted.json()["state"] == "muted"

    blocked = await client.post("/api/voice/transcribe", json={"audio_base64": SAMPLE_AUDIO})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "microphone_muted"

    status = await client.get("/api/voice/status")
    assert status.json()["muted"] is True

    stopped = await client.post("/api/voice/stop")
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "idle"


async def test_unmuted_reports_missing_engine_instead_of_faking(client):
    await client.post("/api/voice/start", json={"muted": True})
    await client.post("/api/voice/unmute")
    response = await client.post("/api/voice/transcribe", json={"audio_base64": SAMPLE_AUDIO})
    assert response.status_code == 503
    assert response.json()["code"] == "tool_unavailable"
    await client.post("/api/voice/stop")


async def test_barge_in_interrupts_speech(config):
    engine = FakeTtsEngine()
    manager = VoiceSessionManager(config, tts_engine=engine)
    try:
        manager.start()
        speaking = await manager.speak("MATRIX online")
        assert speaking["speaking"] is True
        await asyncio.wait_for(engine.started.wait(), timeout=2)

        result = manager.barge_in()
        assert result["interrupted"] is True
        assert result["state"] == "listening"
        assert engine.spoken == ["MATRIX online"]
        assert manager.status().state == "listening"
    finally:
        await manager.shutdown()


async def test_barge_in_without_speech_is_safe(client):
    await client.post("/api/voice/start", json={"muted": False})
    response = await client.post("/api/voice/barge-in")
    assert response.status_code == 200
    assert response.json()["interrupted"] is False
    assert response.json()["state"] == "listening"
    await client.post("/api/voice/stop")


async def test_speak_without_engine_is_honest(client):
    await client.post("/api/voice/start", json={"muted": False})
    response = await client.post("/api/voice/speak", json={"text": "hello"})
    assert response.status_code == 503
    assert response.json()["code"] == "tool_unavailable"
    await client.post("/api/voice/stop")



def test_local_transcription_loads_cpu_model_once_and_keeps_audio_in_memory(config, monkeypatch):
    instances = []

    class FakeWhisperModel:
        def __init__(self, model_size, **kwargs):
            self.model_size = model_size
            self.options = kwargs
            self.audio_payloads = []
            instances.append(self)

        def transcribe(self, audio, **kwargs):
            self.audio_payloads.append(audio.read())
            assert kwargs["task"] == "transcribe"
            assert kwargs["beam_size"] == 1
            return iter([SimpleNamespace(text=" MATRIX is online. ")]), SimpleNamespace(
                duration=2.5, language="en"
            )

    fake_module = ModuleType("faster_whisper")
    fake_module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

    manager = VoiceSessionManager(config)
    manager.stt_provider = "faster-whisper"
    manager.start()
    audio = base64.b64encode(b"fake audio bytes").decode("ascii")

    first = manager.transcribe(audio)
    second = manager.transcribe(audio)

    assert first == {
        "text": "MATRIX is online.",
        "language": "en",
        "duration_seconds": 2.5,
        "provider": "faster-whisper",
        "persisted": False,
    }
    assert second["text"] == first["text"]
    assert len(instances) == 1
    assert instances[0].model_size == "tiny"
    assert instances[0].options == {
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": 2,
        "num_workers": 1,
    }
    assert instances[0].audio_payloads == [b"fake audio bytes", b"fake audio bytes"]
    assert manager._stt_model is instances[0]


def test_local_transcription_rejects_audio_over_duration_limit(config, monkeypatch):
    config.stt_max_audio_seconds = 1
    manager = VoiceSessionManager(config)
    manager.stt_provider = "faster-whisper"
    manager.start()

    class TooLongWhisper:
        def transcribe(self, audio, **kwargs):
            return iter([]), SimpleNamespace(duration=2.0, language="en")

    monkeypatch.setattr(manager, "_load_stt_model", lambda: TooLongWhisper())

    with pytest.raises(ValidationFailure, match="longer than the 1-second limit"):
        manager.transcribe(base64.b64encode(b"audio").decode("ascii"))


async def test_transcribe_api_offloads_and_audits_metadata(client, runtime, monkeypatch):
    async def fake_transcribe(audio_base64, *, session_id=None):
        assert audio_base64 == "ZmFrZWF1ZGlv"
        return {
            "text": "hello MATRIX",
            "language": "en",
            "duration_seconds": 1.2,
            "provider": "faster-whisper",
            "persisted": False,
        }

    monkeypatch.setattr(runtime.voice, "transcribe_async", fake_transcribe)
    started = await client.post("/api/voice/start")
    assert started.status_code == 200

    response = await client.post(
        "/api/voice/transcribe",
        json={"audio_base64": "ZmFrZWF1ZGlv", "session_id": started.json()["session_id"]},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "hello MATRIX"

    events = runtime.store.list_audit(limit=20)
    event = next(item for item in events if item.event_type == "voice.transcribed")
    assert event.detail == {
        "provider": "faster-whisper",
        "language": "en",
        "duration_seconds": 1.2,
    }
    assert "hello MATRIX" not in str(event.detail)

async def test_voice_status_is_visible(client):
    response = await client.get("/api/voice/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["stt_provider"] in {"unavailable", "faster-whisper"}
