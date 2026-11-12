"""Vision capture sessions.

Capture is never implicit: a session requires the client to assert camera
permission, frames are counted but not persisted, and stopping the session
makes further captures fail. OCR availability is reported honestly.
"""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from dataclasses import dataclass, field

from ..config import MatrixConfig
from ..core.models import VisionStatusView, iso
from ..errors import ConflictError, NotFoundError, VisionPermissionDenied


@dataclass
class VisionSession:
    id: str
    permission_granted: bool
    state: str = "active"
    frames: int = 0
    started_at: str = field(default_factory=iso)
    stopped_at: str | None = None


class VisionSessionManager:
    def __init__(self, config: MatrixConfig) -> None:
        self.config = config
        self._sessions: dict[str, VisionSession] = {}
        self.ocr_available = bool(
            importlib.util.find_spec("pytesseract") and shutil.which("tesseract")
        )

    def status(self) -> VisionStatusView:
        active = [session for session in self._sessions.values() if session.state == "active"]
        detail = None
        if not self.ocr_available:
            detail = "Screenshot/image capture control is available; OCR needs Tesseract plus the 'vision' extra."
        return VisionStatusView(
            available=True,
            state="active" if active else "idle",
            active_sessions=len(active),
            ocr_available=self.ocr_available,
            detail=detail,
        )

    def create_session(self, *, permission_granted: bool) -> VisionSession:
        if not permission_granted:
            raise VisionPermissionDenied("camera permission was not granted by the client")
        session = VisionSession(id=uuid.uuid4().hex, permission_granted=True)
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> VisionSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise NotFoundError(f"vision session {session_id} not found")
        return session

    def capture(self, session_id: str) -> dict:
        session = self.get(session_id)
        if not session.permission_granted:
            raise VisionPermissionDenied("camera permission was not granted")
        if session.state != "active":
            raise ConflictError("vision session is not active; start a new session")
        session.frames += 1
        return {
            "session_id": session.id,
            "frame": session.frames,
            "state": session.state,
            "persisted": False,
            "note": "Frames are processed on demand in memory and are never stored by default.",
        }

    def stop(self, session_id: str) -> VisionSession:
        session = self.get(session_id)
        session.state = "stopped"
        session.stopped_at = iso()
        return session

    def stop_all(self) -> int:
        count = 0
        for session in self._sessions.values():
            if session.state == "active":
                session.state = "stopped"
                session.stopped_at = iso()
                count += 1
        return count
