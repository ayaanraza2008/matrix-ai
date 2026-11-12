"""A11/A12 - voice mute and barge-in controls."""

from __future__ import annotations

import asyncio

import pytest

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


async def test_voice_status_is_visible(client):
    response = await client.get("/api/voice/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["stt_provider"] in {"unavailable", "faster-whisper"}
