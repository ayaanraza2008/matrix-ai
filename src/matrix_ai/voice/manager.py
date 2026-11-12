"""Voice session control.

This release ships the permission-critical half of the voice stack: an explicit
session, a visible listening/speaking state, instant mute, push-to-talk
semantics and barge-in cancellation. Speech engines (faster-whisper/Piper) are
optional adapters; when absent the API says so instead of faking a transcript.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import importlib.util
import io
import shutil
import uuid
from dataclasses import dataclass, field
from typing import Any
from typing import Protocol

from ..config import MatrixConfig
from ..core.models import VoiceStatusView, iso
from ..errors import ConflictError, NotFoundError, ToolUnavailable, ValidationFailure, VoiceMuted
from ..security.redaction import contains_secret

MAX_AUDIO_BYTES = 8_000_000


class TtsEngine(Protocol):
    name: str

    async def speak(self, text: str) -> None: ...


@dataclass
class VoiceSession:
    id: str
    state: str = "idle"
    muted: bool = False
    started_at: str = field(default_factory=iso)
    last_utterance: str | None = None


class VoiceSessionManager:
    def __init__(self, config: MatrixConfig, tts_engine: TtsEngine | None = None) -> None:
        self.config = config
        self.tts_engine = tts_engine
        self._sessions: dict[str, VoiceSession] = {}
        self._active_id: str | None = None
        self._speak_task: asyncio.Task | None = None
        self._stt_model: Any | None = None
        self.stt_provider = self._detect_stt()
        self.tts_provider = tts_engine.name if tts_engine is not None else self._detect_tts()

    # -- capability detection --------------------------------------------
    @staticmethod
    def _detect_stt() -> str:
        return "faster-whisper" if importlib.util.find_spec("faster_whisper") else "unavailable"

    @staticmethod
    def _detect_tts() -> str:
        if shutil.which("piper"):
            return "piper"
        return "unavailable"

    # -- session state ----------------------------------------------------
    @property
    def active_session(self) -> VoiceSession | None:
        if self._active_id is None:
            return None
        return self._sessions.get(self._active_id)

    def status(self) -> VoiceStatusView:
        session = self.active_session
        detail = None
        if self.stt_provider == "unavailable":
            detail = "Push-to-talk session control is available; no speech-to-text engine is installed yet."
        return VoiceStatusView(
            available=True,
            state=session.state if session else "idle",
            muted=bool(session and session.muted),
            stt_provider=self.stt_provider,
            tts_provider=self.tts_provider,
            detail=detail,
        )

    def start(self, *, muted: bool = False) -> VoiceSession:
        existing = self.active_session
        if existing is not None:
            return existing
        session = VoiceSession(id=uuid.uuid4().hex, state="listening", muted=muted)
        self._sessions[session.id] = session
        self._active_id = session.id
        return session

    def get(self, session_id: str) -> VoiceSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise NotFoundError(f"voice session {session_id} not found")
        return session

    def mute(self, session_id: str | None = None) -> VoiceSession:
        session = self._require_session(session_id)
        session.muted = True
        session.state = "muted"
        return session

    def unmute(self, session_id: str | None = None) -> VoiceSession:
        session = self._require_session(session_id)
        if session.state == "muted":
            session.state = "listening"
        session.muted = False
        return session

    def stop(self, session_id: str | None = None) -> VoiceSession:
        session = self._require_session(session_id)
        session.state = "idle"
        session.muted = False
        if self._active_id == session.id:
            self._active_id = None
        return session

    def _require_session(self, session_id: str | None) -> VoiceSession:
        if session_id:
            return self.get(session_id)
        session = self.active_session
        if session is None:
            raise ConflictError("no active voice session; start one first")
        return session

    # -- microphone input -------------------------------------------------
    def transcribe(self, audio_base64: str, *, session_id: str | None = None) -> dict:
        session = self._require_session(session_id)
        if session.muted:
            raise VoiceMuted("microphone is muted; audio capture is refused")
        if session.state not in {"listening"}:
            raise ConflictError(f"session state '{session.state}' does not accept audio")
        if self.stt_provider == "unavailable":
            raise ToolUnavailable(
                "No speech-to-text engine is installed. Install the 'voice' extra and a compact "
                "Whisper model, or use text chat."
            )
        try:
            payload = base64.b64decode(audio_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValidationFailure("audio must be base64-encoded") from exc
        if len(payload) > MAX_AUDIO_BYTES:
            raise ValidationFailure("audio exceeds the 8 MB limit")
        # Audio is decoded from memory; raw audio is never written to disk.
        model = self._load_stt_model()
        try:
            segments, info = model.transcribe(
                io.BytesIO(payload),
                language=self.config.stt_language,
                task="transcribe",
                beam_size=1,
                best_of=1,
                temperature=0,
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            duration = float(info.duration)
            if duration <= 0:
                raise ValidationFailure("audio contains no decodable speech")
            if duration > self.config.stt_max_audio_seconds:
                raise ValidationFailure(
                    f"audio is longer than the {self.config.stt_max_audio_seconds}-second limit"
                )
            transcript = " ".join(
                str(segment.text).strip() for segment in segments if str(segment.text).strip()
            ).strip()
        except ValidationFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - codec errors vary by media container
            raise ValidationFailure("audio could not be decoded; try recording again") from exc

        if not transcript:
            raise ValidationFailure("no speech was detected in the audio")
        return {
            "text": transcript,
            "language": getattr(info, "language", None),
            "duration_seconds": round(duration, 2),
            "provider": "faster-whisper",
            "persisted": False,
        }

    def _load_stt_model(self):
        """Load one compact CPU/INT8 model on first use, never during startup."""
        if self._stt_model is not None:
            return self._stt_model
        if self.stt_provider == "unavailable":
            raise ToolUnavailable(
                "faster-whisper is not installed. Install MATRIX with the 'voice' extra."
            )
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # defensive if the package disappears after capability detection
            self.stt_provider = "unavailable"
            raise ToolUnavailable(
                "faster-whisper is not installed. Install MATRIX with the 'voice' extra."
            ) from exc
        try:
            self._stt_model = WhisperModel(
                self.config.stt_model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=self.config.stt_cpu_threads,
                num_workers=1,
            )
        except Exception as exc:  # noqa: BLE001 - download/runtime errors are provider-specific
            raise ToolUnavailable(
                f"Could not load local speech model '{self.config.stt_model_size}'. "
                "Check network access for its first download and available memory."
            ) from exc
        return self._stt_model

    async def transcribe_async(
        self, audio_base64: str, *, session_id: str | None = None
    ) -> dict:
        """Keep model loading and decoding off the API event loop."""
        return await asyncio.to_thread(
            self.transcribe, audio_base64, session_id=session_id
        )

    # -- speech output & barge-in ----------------------------------------
    async def speak(self, text: str, *, session_id: str | None = None) -> dict:
        session = self._require_session(session_id)
        text = (text or "").strip()
        if not text:
            raise ValidationFailure("text to speak is required")
        if contains_secret(text):
            raise ValidationFailure("refusing to speak text that looks like a credential")
        if self.tts_engine is None:
            raise ToolUnavailable(
                "No text-to-speech engine is installed. Install Piper (or another local TTS engine) "
                "to enable spoken replies."
            )
        if self._speak_task is not None and not self._speak_task.done():
            raise ConflictError("already speaking; barge in first")
        session.state = "speaking"
        session.last_utterance = text[:500]
        self._speak_task = asyncio.create_task(self._speak(session, text))
        return {"session_id": session.id, "state": session.state, "speaking": True}

    async def _speak(self, session: VoiceSession, text: str) -> None:
        assert self.tts_engine is not None  # noqa: S101
        try:
            await self.tts_engine.speak(text)
        finally:
            if session.state == "speaking":
                session.state = "listening"

    def barge_in(self, *, session_id: str | None = None) -> dict:
        session = self._require_session(session_id)
        interrupted = bool(self._speak_task is not None and not self._speak_task.done())
        if interrupted and self._speak_task is not None:
            self._speak_task.cancel()
            self._speak_task = None
        session.state = "listening"
        return {"session_id": session.id, "state": session.state, "interrupted": interrupted}

    async def shutdown(self) -> None:
        if self._speak_task is not None and not self._speak_task.done():
            self._speak_task.cancel()
            try:
                await self._speak_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown path
                pass
        self._speak_task = None
        self._sessions.clear()
        self._active_id = None
