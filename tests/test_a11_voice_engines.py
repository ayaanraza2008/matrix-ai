from __future__ import annotations

import base64

import pytest

from matrix_ai.config import MatrixConfig
from matrix_ai.errors import ToolUnavailable, VoiceMuted
from matrix_ai.voice.manager import VoiceSessionManager


class FakeStt:
    name = "fake-stt"
    def __init__(self):
        self.received = None
    def transcribe(self, audio: bytes) -> dict:
        self.received = audio
        return {"text": "hello matrix", "language": "en", "provider": self.name}


class FakeTts:
    name = "fake-tts"
    async def speak(self, text: str) -> None:
        self.text = text


def test_local_stt_receives_decoded_audio_and_returns_transcript(tmp_path):
    engine = FakeStt()
    manager = VoiceSessionManager(MatrixConfig(home=tmp_path), stt_engine=engine)
    session = manager.start()
    audio = b"fake-webm-audio"
    result = manager.transcribe(base64.b64encode(audio).decode(), session_id=session.id)
    assert engine.received == audio
    assert result["text"] == "hello matrix"
    assert result["session_id"] == session.id
    assert result["language"] == "en"


def test_muted_session_never_calls_stt(tmp_path):
    engine = FakeStt()
    manager = VoiceSessionManager(MatrixConfig(home=tmp_path), stt_engine=engine)
    session = manager.start(muted=True)
    with pytest.raises(VoiceMuted):
        manager.transcribe(base64.b64encode(b"audio").decode(), session_id=session.id)
    assert engine.received is None


def test_missing_stt_engine_is_an_honest_unavailable_error(tmp_path):
    manager = VoiceSessionManager(MatrixConfig(home=tmp_path), stt_engine=None)
    manager.stt_engine = None
    manager.stt_provider = "unavailable"
    session = manager.start()
    with pytest.raises(ToolUnavailable):
        manager.transcribe(base64.b64encode(b"audio").decode(), session_id=session.id)


@pytest.mark.asyncio
async def test_local_tts_is_wired_to_speech_session(tmp_path):
    engine = FakeTts()
    manager = VoiceSessionManager(MatrixConfig(home=tmp_path), tts_engine=engine)
    session = manager.start()
    result = await manager.speak("Hello there", session_id=session.id)
    assert result["speaking"] is True
    await manager._speak_task
    assert engine.text == "Hello there"
    assert session.state == "listening"
