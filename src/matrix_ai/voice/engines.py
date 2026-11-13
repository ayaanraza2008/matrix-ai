"""Optional offline speech engines used by MATRIX voice sessions.

No audio is written to the database. STT uses faster-whisper on CPU by default;
TTS uses a locally supplied Piper voice and the machine's audio output device.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Protocol


class SttEngine(Protocol):
    name: str
    def transcribe(self, audio: bytes, *, suffix: str | None = None) -> dict: ...


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

    def transcribe(self, audio: bytes, *, suffix: str | None = None) -> dict:
        if not audio:
            raise ValueError("audio payload is empty")
        suffix = suffix or os.getenv("MATRIX_STT_AUDIO_SUFFIX", ".webm")
        if not suffix.startswith(".") or len(suffix) > 8 or not suffix[1:].isalnum():
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
            process = await asyncio.create_subprocess_exec(
                str(self.executable),
                "--model",
                str(self.model_path),
                "--output_file",
                str(output),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(text.encode("utf-8")), timeout=120
                )
                if process.returncode != 0 or not output.is_file():
                    detail = (stderr.decode("utf-8", errors="replace") or "Piper did not produce audio.")[-500:]
                    raise RuntimeError(detail)
                audio, sample_rate = await asyncio.to_thread(
                    sf.read, str(output), dtype="float32"
                )
                playback = asyncio.create_task(
                    asyncio.to_thread(self._play_sync, sd, audio, sample_rate)
                )
                try:
                    await playback
                except asyncio.CancelledError:
                    sd.stop()
                    raise
            except asyncio.CancelledError:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=2)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
                sd.stop()
                raise

    @staticmethod
    def _play_sync(sounddevice, audio, sample_rate: int) -> None:
        sounddevice.play(audio, sample_rate)
        sounddevice.wait()


def build_stt_engine() -> SttEngine | None:
    try:
        import faster_whisper  # noqa: F401
    except (ImportError, OSError):
        # A partially installed native CTranslate2 runtime must not break MATRIX startup.
        return None
    return FasterWhisperEngine()


class WindowsSapiTtsEngine:
    """Opt-in speech output through the locally installed Windows SAPI voice.

    Text is sent to a fixed PowerShell script over stdin as JSON; it is never
    interpolated into a command string or written to disk. Requires the
    ``MATRIX_TTS_PROVIDER=windows-sapi`` setting on Windows.
    """

    name = "windows-sapi"

    _POWERSHELL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
if ($null -eq $payload.text) { throw 'Speech text is required.' }
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $synth.SetOutputToDefaultAudioDevice()
    $synth.Speak([string]$payload.text)
}
finally {
    $synth.Dispose()
}
"""

    def __init__(self, executable: str, *, timeout_seconds: float = 180.0) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def speak(self, text: str) -> None:
        """Run a fixed PowerShell script; the user text is JSON on stdin."""
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Speech text is empty.")

        async with self._lock:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                process = await asyncio.create_subprocess_exec(
                    self.executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-STA",
                    "-Command",
                    self._POWERSHELL_SCRIPT,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=creationflags,
                )
            except (OSError, RuntimeError) as exc:
                raise RuntimeError("Could not start the local Windows speech engine.") from exc

            self._process = process
            payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(payload), timeout=self.timeout_seconds
                )
                if process.returncode != 0:
                    # Do not surface PowerShell output, which may contain input text.
                    detail = stderr.decode("utf-8", errors="replace").strip()
                    if "voice" in detail.lower():
                        raise RuntimeError(
                            "Windows speech synthesis failed. Check that a local speech voice is installed."
                        )
                    raise RuntimeError(
                        "Windows speech synthesis failed. Check the Windows speech service and installed voices."
                    )
            except TimeoutError as exc:
                await self._terminate_process(process)
                raise RuntimeError("Windows speech synthesis timed out.") from exc
            except asyncio.CancelledError:
                await self._terminate_process(process)
                raise
            finally:
                if self._process is process:
                    self._process = None

    def stop(self) -> None:
        """Interrupt active speech immediately; safe to call from barge-in."""
        process = self._process
        if process is not None and process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except TimeoutError:
            process.kill()
            await process.wait()


def _build_windows_sapi_engine() -> WindowsSapiTtsEngine | None:
    """Create the SAPI engine only on Windows with PowerShell present."""
    if sys.platform != "win32":
        return None
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    return WindowsSapiTtsEngine(executable) if executable else None


def build_tts_engine(config: object | None = None):
    """Build the configured local TTS engine, or None when none is usable.

    Default behaviour is unchanged (Piper when installed and configured).
    ``MATRIX_TTS_PROVIDER`` selects an explicit provider: ``windows-sapi``
    (opt-in Windows SAPI), ``piper`` or ``none`` (disable speech output).
    """
    provider = str(getattr(config, "tts_provider", "") or "").strip().lower()
    if provider == "none":
        return None
    if provider == "windows-sapi":
        return _build_windows_sapi_engine()
    if provider not in {"", "piper"}:
        raise ValueError(f"unsupported MATRIX_TTS_PROVIDER value: {provider}")
    engine = PiperCliEngine()
    return engine if engine.available else None
