"""Optional offline speech engines. Audio is processed in memory only."""

from __future__ import annotations

import asyncio
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from ..errors import ToolUnavailable


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str | None = None
    language_probability: float | None = None


class FasterWhisperSTT:
    """Lazy faster-whisper adapter using CPU-friendly defaults and in-memory audio."""

    name = "faster-whisper"

    def __init__(
        self,
        *,
        model_name: str = "tiny",
        device: str = "cpu",
        compute_type: str = "int8",
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model_factory = model_factory
        self._model: Any | None = None
        self._model_lock = threading.Lock()

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                try:
                    factory = self._model_factory
                    if factory is None:
                        from faster_whisper import WhisperModel
                        factory = WhisperModel
                    self._model = factory(
                        self.model_name,
                        device=self.device,
                        compute_type=self.compute_type,
                    )
                except Exception as exc:  # noqa: BLE001 - optional native runtime boundary
                    raise ToolUnavailable(
                        f"Could not initialize the local faster-whisper model ({type(exc).__name__}). "
                        "Check the voice extra, model download and available RAM."
                    ) from exc
        return self._model

    def transcribe(self, audio: bytes, language: str | None = None) -> Transcription:
        if not audio:
            raise ToolUnavailable("No audio bytes were supplied for local transcription.")
        try:
            model = self._get_model()
            segments, info = model.transcribe(
                io.BytesIO(audio),
                language=language,
                beam_size=1,
                vad_filter=True,
            )
            text = " ".join(
                segment.text.strip() for segment in segments if segment.text and segment.text.strip()
            ).strip()
            probability = getattr(info, "language_probability", None)
            return Transcription(
                text=text,
                language=getattr(info, "language", None),
                language_probability=round(float(probability), 4) if probability is not None else None,
            )
        except ToolUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - native decoder/model errors
            raise ToolUnavailable(
                f"Local speech transcription failed ({type(exc).__name__}). "
                "Check the audio format and installed voice dependencies."
            ) from exc


class Pyttsx3TTS:
    """Offline OS-voice TTS adapter (SAPI5 on Windows) with best-effort stop."""

    name = "pyttsx3"

    def __init__(
        self,
        *,
        voice_id: str | None = None,
        rate: int = 180,
        engine_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.voice_id = voice_id
        self.rate = rate
        self._engine_factory = engine_factory
        self._engine: Any | None = None
        self._engine_lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="matrix-tts")
        self._closed = False

    def _get_engine(self) -> Any:
        with self._engine_lock:
            if self._closed:
                raise ToolUnavailable("The local speech engine has been shut down.")
            if self._engine is None:
                try:
                    factory = self._engine_factory
                    if factory is None:
                        import pyttsx3
                        factory = pyttsx3.init
                    engine = factory()
                    engine.setProperty("rate", self.rate)
                    if self.voice_id:
                        available = engine.getProperty("voices") or []
                        selected = next(
                            (
                                voice.id
                                for voice in available
                                if voice.id == self.voice_id or getattr(voice, "name", None) == self.voice_id
                            ),
                            None,
                        )
                        if selected is None:
                            raise ToolUnavailable(
                                f"Configured speech voice '{self.voice_id}' is not installed on this device."
                            )
                        engine.setProperty("voice", selected)
                    self._engine = engine
                except ToolUnavailable:
                    raise
                except Exception as exc:  # noqa: BLE001 - platform speech-driver boundary
                    raise ToolUnavailable(
                        f"Could not initialize the offline OS speech engine ({type(exc).__name__})."
                    ) from exc
            return self._engine

    def _speak_sync(self, text: str) -> None:
        try:
            engine = self._get_engine()
            engine.say(text)
            engine.runAndWait()
        except ToolUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - speech-driver boundary
            raise ToolUnavailable(f"Offline speech output failed ({type(exc).__name__}).") from exc

    async def speak(self, text: str) -> None:
        if self._closed:
            raise ToolUnavailable("The local speech engine has been shut down.")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._speak_sync, text)

    def stop(self) -> None:
        """Best-effort interruption; driver support varies by OS and voice backend."""
        with self._engine_lock:
            engine = self._engine
        if engine is not None:
            try:
                engine.stop()
            except Exception:  # noqa: BLE001 - stop must not block the safety control path
                pass

    def shutdown(self) -> None:
        self.stop()
        with self._engine_lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)


__all__ = ["FasterWhisperSTT", "Pyttsx3TTS", "Transcription"]
