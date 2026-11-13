"""Voice session control with optional local speech engines.

Mute is checked before decoding or invoking STT. Raw audio is processed in a
temporary file by the engine and is never persisted by MATRIX.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from ..config import MatrixConfig
from ..core.models import VoiceStatusView, iso
from ..errors import ConflictError, NotFoundError, ToolUnavailable, ValidationFailure, VoiceMuted
from ..security.redaction import contains_secret
from .engines import SttEngine, build_stt_engine, build_tts_engine

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
    def __init__(
        self, config: MatrixConfig, tts_engine: TtsEngine | None = None,
        stt_engine: SttEngine | None = None,
    ) -> None:
        self.config = config
        self.tts_engine = tts_engine if tts_engine is not None else build_tts_engine(config)
        self.stt_engine = stt_engine if stt_engine is not None else build_stt_engine()
        self._sessions: dict[str, VoiceSession] = {}
        self._active_id: str | None = None
        self._speak_task: asyncio.Task | None = None
        self.stt_provider = self.stt_engine.name if self.stt_engine else "unavailable"
        self.tts_provider = self.tts_engine.name if self.tts_engine else "unavailable"

    @property
    def active_session(self) -> VoiceSession | None:
        return self._sessions.get(self._active_id) if self._active_id else None

    def status(self) -> VoiceStatusView:
        session = self.active_session
        missing = []
        if self.stt_engine is None:
            missing.append("speech-to-text")
        if self.tts_engine is None:
            missing.append("text-to-speech")
        detail = None if not missing else "Local " + " and ".join(missing) + " engine not configured."
        return VoiceStatusView(
            available=True, state=session.state if session else "idle",
            muted=bool(session and session.muted), stt_provider=self.stt_provider,
            tts_provider=self.tts_provider, detail=detail,
        )

    def start(self, *, muted: bool = False) -> VoiceSession:
        existing = self.active_session
        if existing is not None:
            return existing
        session = VoiceSession(
            id=uuid.uuid4().hex, state="muted" if muted else "listening", muted=muted
        )
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
        self._cancel_speech()
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
        self._cancel_speech()
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

    def transcribe(self, audio_base64: str, *, session_id: str | None = None) -> dict:
        session = self._require_session(session_id)
        self._require_capture_ready(session)
        if self.stt_engine is None:
            raise ToolUnavailable(
                "No local speech-to-text engine is installed. Install matrix-ai[voice] "
                "and allow the first Whisper model download, or use text chat."
            )
        payload = self._decode_audio(audio_base64)
        result = self._transcribe_payload(payload)
        transcript = str(result.get("text", "")).strip()
        session.last_utterance = transcript[:500] or None
        return {**result, "text": transcript, "session_id": session.id}

    async def transcribe_async(self, audio_base64: str, *, session_id: str | None = None) -> dict:
        """Keep every guard in the sync order; model inference runs off the loop."""
        session = self._require_session(session_id)
        self._require_capture_ready(session)
        if self.stt_engine is None:
            raise ToolUnavailable(
                "No local speech-to-text engine is installed. Install matrix-ai[voice] "
                "and allow the first Whisper model download, or use text chat."
            )
        payload = self._decode_audio(audio_base64)
        # Whisper inference is CPU-bound; running it directly would block the API.
        result = await asyncio.to_thread(self._transcribe_payload, payload)
        transcript = str(result.get("text", "")).strip()
        session.last_utterance = transcript[:500] or None
        return {**result, "text": transcript, "session_id": session.id}

    def _require_capture_ready(self, session: VoiceSession) -> None:
        if session.muted:
            raise VoiceMuted("microphone is muted; audio capture is refused")
        if session.state != "listening":
            raise ConflictError(f"session state '{session.state}' does not accept audio")

    def _decode_audio(self, audio_base64: str) -> bytes:
        try:
            payload = base64.b64decode(audio_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValidationFailure("audio must be base64-encoded") from exc
        if not payload:
            raise ValidationFailure("audio payload is empty")
        if len(payload) > MAX_AUDIO_BYTES:
            raise ValidationFailure("audio exceeds the 8 MB limit")
        return payload

    def _transcribe_payload(self, payload: bytes) -> dict:
        assert self.stt_engine is not None  # noqa: S101
        try:
            return self.stt_engine.transcribe(payload)
        except Exception as exc:  # noqa: BLE001 - codec/model errors vary by optional backend
            raise ToolUnavailable(
                f"Local speech-to-text failed ({type(exc).__name__})."
            ) from exc

    def validate_speech(self, text: str) -> dict:
        """Preflight check so clients can validate before speaking or playback."""
        if not isinstance(text, str) or not text.strip():
            raise ValidationFailure("text to speak is required")
        if contains_secret(text):
            raise ValidationFailure("refusing to speak text that looks like a credential")
        return {"allowed": True}

    async def speak(self, text: str, *, session_id: str | None = None) -> dict:
        session = self._require_session(session_id)
        text = (text or "").strip()
        self.validate_speech(text)
        if self.tts_engine is None:
            raise ToolUnavailable(
                "No local text-to-speech engine is configured. Install matrix-ai[voice], "
                "download a Piper voice, and set MATRIX_PIPER_MODEL."
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
        if interrupted:
            self._cancel_speech()
        session.state = "listening"
        return {"session_id": session.id, "state": session.state, "interrupted": interrupted}

    def _cancel_speech(self) -> None:
        task = self._speak_task
        if task is not None and not task.done():
            task.cancel()
        self._speak_task = None
        # Engines with a native stop (e.g. Windows SAPI) must end audible output
        # immediately, not only when their coroutine observes cancellation.
        stop = getattr(self.tts_engine, "stop", None)
        if callable(stop):
            stop()

    async def shutdown(self) -> None:
        if self._speak_task is not None and not self._speak_task.done():
            self._speak_task.cancel()
            try:
                await self._speak_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown path
                pass
        self._cancel_speech()
        self._sessions.clear()
        self._active_id = None
