"""Optional offline speech engines used by MATRIX voice sessions.

No audio is written to the database. STT uses faster-whisper on CPU by default;
TTS uses a locally supplied Piper voice and the machine's audio output device.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Protocol


class SttEngine(Protocol):
    name: str
    def transcribe(self, audio: bytes) -> dict: ...


class FasterWhisperEngine:
    """Lazy CPU/INT8 Whisper adapter; model weights are cached locally."""
    name = "faster-whisper"

    def __init__(self, model_size: str | None = None) -> None:
        self.model_size = model_size or os.getenv("MATRIX_STT_MODEL", "tiny")
        self.device = os.getenv("MATRIX_STT_DEVICE", "cpu")
        self.compute_type = os.getenv("MATRIX_STT_COMPUTE_TYPE", "int8")
        self._model = None
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    try:
                        from faster_whisper import WhisperModel
                    except ImportError as exc:
                        raise RuntimeError("Install MATRIX with the [voice] extra to enable Whisper.") from exc
                    self._model = WhisperModel(
                        self.model_size, device=self.device, compute_type=self.compute_type
                    )
        return self._model

    def transcribe(self, audio: bytes) -> dict:
        if not audio:
            raise ValueError("audio payload is empty")
        suffix = os.getenv("MATRIX_STT_AUDIO_SUFFIX", ".webm")
        if not suffix.startswith(".") or len(suffix) > 8:
            suffix = ".webm"
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                handle.write(audio)
                path = Path(handle.name)
            segments, info = self._get_model().transcribe(str(path), beam_size=1, vad_filter=True)
            transcript = " ".join(segment.text.strip() for segment in segments).strip()
            return {
                "text": transcript,
                "language": getattr(info, "language", None),
                "duration_seconds": getattr(info, "duration", None),
                "provider": self.name,
            }
        finally:
            if path is not None:
                path.unlink(missing_ok=True)


class PiperCliEngine:
    """Piper CLI adapter that speaks through the host's default audio device."""
    name = "piper"

    def __init__(self, model_path: str | None = None) -> None:
        self.executable = shutil.which(os.getenv("MATRIX_PIPER_BIN", "piper"))
        configured = model_path or os.getenv("MATRIX_PIPER_MODEL")
        self.model_path = Path(configured).expanduser() if configured else None

    @property
    def available(self) -> bool:
        return bool(self.executable and self.model_path and self.model_path.is_file())

    async def speak(self, text: str) -> None:
        try:
            await asyncio.to_thread(self._speak_sync, text)
        except asyncio.CancelledError:
            # Cancelling the coroutine alone does not stop the worker thread's audio stream.
            try:
                import sounddevice as sd
                sd.stop()
            except ImportError:
                pass
            raise

    def _speak_sync(self, text: str) -> None:
        if not self.available:
            raise RuntimeError(
                "Set MATRIX_PIPER_MODEL to a local Piper .onnx voice model and install piper-tts."
            )
        try:
            import sounddevice as sd
            import soundfile as sf
        except ImportError as exc:
            raise RuntimeError("Install MATRIX with the [voice] extra for local audio playback.") from exc
        with tempfile.TemporaryDirectory(prefix="matrix-voice-") as directory:
            output = Path(directory) / "speech.wav"
            completed = subprocess.run(
                [str(self.executable), "--model", str(self.model_path), "--output_file", str(output)],
                input=text, text=True, capture_output=True, timeout=120, check=False,
            )
            if completed.returncode != 0 or not output.is_file():
                detail = (completed.stderr or "Piper did not produce audio.")[-500:]
                raise RuntimeError(detail)
            audio, sample_rate = sf.read(str(output), dtype="float32")
            sd.play(audio, sample_rate)
            sd.wait()


def build_stt_engine() -> SttEngine | None:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return None
    return FasterWhisperEngine()


def build_tts_engine():
    engine = PiperCliEngine()
    return engine if engine.available else None
