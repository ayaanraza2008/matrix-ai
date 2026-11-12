"""Allowlisted application launch.

The model may only name an ``app_id`` from configuration; it cannot provide an
executable path or command line. Unknown IDs are refused server-side.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import Field

from ..core.models import Risk, VerificationEvidence
from ..errors import PermissionDenied, ToolExecutionError, ToolUnavailable
from .base import StrictModel, Tool, ToolContext


class OpenAppInput(StrictModel):
    app_id: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9_.:\-]+$")


def _windows_executable(name: str) -> str | None:
    from shutil import which

    found = which(name)
    if found:
        return found
    if sys.platform == "win32":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        for candidate in (Path(root) / "System32" / name, Path(root) / name):
            if candidate.is_file():
                return str(candidate)
    return None


class OpenAppTool(Tool):
    name = "apps.open"
    description = "Open an application from the configured allowlist by app_id."
    permission_scope = "apps.launch"
    risk = Risk.MEDIUM
    idempotent = False
    timeout_seconds = 20.0
    input_model = OpenAppInput

    def run_sync(self, args: OpenAppInput, ctx: ToolContext) -> dict[str, Any]:
        target = ctx.config.allowed_apps.get(args.app_id)
        if not target:
            raise PermissionDenied(
                f"app_id '{args.app_id}' is not allowlisted",
                detail={"allowed": sorted(ctx.config.allowed_apps)},
            )

        if target.endswith(":"):
            if sys.platform != "win32":
                raise ToolUnavailable("protocol handlers are only supported on Windows")
            os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606 - allowlisted protocol
            return {"app_id": args.app_id, "target": target, "pid": None, "method": "protocol"}

        executable = _windows_executable(target) or (target if Path(target).is_file() else None)
        if not executable:
            raise ToolUnavailable(f"allowlisted executable not found on this machine: {target}")

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell, allowlisted target
            [executable],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
            close_fds=True,
        )
        return {
            "app_id": args.app_id,
            "target": target,
            "executable": executable,
            "pid": process.pid,
            "method": "exec",
        }

    def verify(self, args: OpenAppInput, output: dict[str, Any]) -> VerificationEvidence:
        method = output.get("method")
        pid = output.get("pid")
        ok = method == "protocol" or (isinstance(pid, int) and pid > 0)
        return VerificationEvidence(
            ok=ok,
            confidence=0.8 if ok else 0.0,
            checks=[f"started_via_{method}" if ok else "process_not_started"],
            detail={"pid": pid},
        )


class CloseAppInput(StrictModel):
    pid: int = Field(gt=0, le=2**31 - 1)


class CloseAppTool(Tool):
    name = "apps.close"
    description = "Terminate a single process by PID. High risk; requires approval."
    permission_scope = "apps.control"
    risk = Risk.HIGH
    idempotent = False
    timeout_seconds = 15.0
    input_model = CloseAppInput

    def run_sync(self, args: CloseAppInput, ctx: ToolContext) -> dict[str, Any]:
        import psutil

        try:
            process = psutil.Process(args.pid)
        except psutil.NoSuchProcess as exc:
            raise ToolExecutionError("process does not exist") from exc
        name = process.name()
        process.terminate()
        try:
            process.wait(timeout=5)
        except psutil.TimeoutExpired:
            process.kill()
        return {"pid": args.pid, "name": name, "terminated": True}

    def verify(self, args: CloseAppInput, output: dict[str, Any]) -> VerificationEvidence:
        import psutil

        gone = not psutil.pid_exists(args.pid)
        return VerificationEvidence(
            ok=gone,
            confidence=0.9 if gone else 0.0,
            checks=["process_gone" if gone else "process_still_running"],
        )
