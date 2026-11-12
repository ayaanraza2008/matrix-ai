"""Optional local speech output adapters.

Windows SAPI uses the voice already installed in Windows. Text is sent to a
fixed PowerShell script over stdin as JSON; it is never interpolated into a
command string or written to disk.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from typing import Any

from ..errors import ToolUnavailable

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


class WindowsSapiTtsEngine:
    """Speak through the default locally installed Windows SAPI voice."""

    name = "windows-sapi"

    def __init__(self, executable: str, *, timeout_seconds: float = 180.0) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def speak(self, text: str) -> None:
        """Run a fixed PowerShell script; the user text is JSON on stdin."""
        if not isinstance(text, str) or not text.strip():
            raise ToolUnavailable("Speech text is empty.")

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
                    _POWERSHELL_SCRIPT,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=creationflags,
                )
            except (OSError, RuntimeError) as exc:
                raise ToolUnavailable("Could not start the local Windows speech engine.") from exc

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
                        raise ToolUnavailable(
                            "Windows speech synthesis failed. Check that a local speech voice is installed."
                        )
                    raise ToolUnavailable(
                        "Windows speech synthesis failed. Check the Windows speech service and installed voices."
                    )
            except TimeoutError as exc:
                await self._terminate_process(process)
                raise ToolUnavailable("Windows speech synthesis timed out.") from exc
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


def create_tts_engine(config: Any):
    """Create the explicitly configured engine; never silently enable audio."""
    provider = str(getattr(config, "tts_provider", "none")).strip().lower()
    if provider in {"", "none"}:
        return None
    if provider != "windows-sapi" or sys.platform != "win32":
        return None
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if not executable:
        return None
    return WindowsSapiTtsEngine(executable)
