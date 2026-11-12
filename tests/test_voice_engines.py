"""Optional offline voice adapters can be tested without audio hardware."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from matrix_ai.voice.engines import FasterWhisperSTT, Pyttsx3TTS


class FakeWhisperModel:
    def __init__(self) -> None:
        self.received_bytes = None
        self.language = None

    def transcribe(self, audio, **kwargs):
        self.received_bytes = audio.read()
        self.language = kwargs["language"]
        return iter([SimpleNamespace(text=" hello "), SimpleNamespace(text=" world ")]), SimpleNamespace(
            language="hi", language_probability=0.91
        )


def test_faster_whisper_adapter_uses_in_memory_audio_and_joins_segments():
    fake_model = FakeWhisperModel()
    engine = FasterWhisperSTT(
        model_name="tiny",
        device="cpu",
        compute_type="int8",
        model_factory=lambda model_name, *, device, compute_type: fake_model,
    )

    result = engine.transcribe(b"fake audio payload", language="hi")

    assert result.text == "hello world"
    assert result.language == "hi"
    assert result.language_probability == 0.91
    assert fake_model.received_bytes == b"fake audio payload"
    assert fake_model.language == "hi"


class FakePyttsx3:
    def __init__(self) -> None:
        self.properties = {}
        self.spoken = []
        self.stop_calls = 0

    def setProperty(self, key, value):
        self.properties[key] = value

    def getProperty(self, key):
        return []

    def say(self, text):
        self.spoken.append(text)

    def runAndWait(self):
        return None

    def stop(self):
        self.stop_calls += 1


@pytest.mark.asyncio
async def test_pyttsx3_adapter_uses_offline_engine_and_can_stop():
    fake = FakePyttsx3()
    engine = Pyttsx3TTS(rate=165, engine_factory=lambda: fake)

    await engine.speak("MATRIX is ready")
    engine.stop()

    assert fake.spoken == ["MATRIX is ready"]
    assert fake.properties["rate"] == 165
    assert fake.stop_calls == 1
    engine.shutdown()


def test_pyttsx3_resolves_selected_voice_id():
    fake = FakePyttsx3()
    fake.getProperty = lambda key: [SimpleNamespace(id="voice.en", name="English")]
    engine = Pyttsx3TTS(voice_id="voice.en", engine_factory=lambda: fake)
    engine._get_engine()
    assert fake.properties["voice"] == "voice.en"
    engine.shutdown()
