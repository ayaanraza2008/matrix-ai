"""Allowlisted command execution.

There is deliberately no free-form command string parameter. The model may only
select a ``command_id`` defined in configuration; arguments are never accepted
from the model. Subprocesses get a scrubbed environment so API tokens are not
inherited by child processes.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Any

from pydantic import Field

from ..core.models import Risk, VerificationEvidence
from ..errors import PermissionDenied, ToolExecutionError, ToolUnavailable
from .base import StrictModel, Tool, ToolContext

_SECRET_ENV = re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key|credential|owner_token)")


def safe_environment() -> dict[str, str]:
    """Inherit a working environment but strip credential-looking variables."""
    return {key: value for key, value in os.environ.items() if not _SECRET_ENV.search(key)}


class RunAllowedInput(StrictModel):
    command_id: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9_.\-]+$")


class RunAllowedCommandTool(Tool):
    name = "shell.run_allowed"
    description = "Run one of the configured allowlisted commands (no free-form arguments)."
    permission_scope = "shell.allowlisted"
    risk = Risk.MEDIUM
    idempotent = False
    timeout_seconds = 300.0
    input_model = RunAllowedInput

    def _entry(self, ctx: ToolContext, command_id: str) -> dict[str, Any]:
        entry = ctx.config.allowed_commands.get(command_id)
        if not entry:
            raise PermissionDenied(
                f"command_id '{command_id}' is not allowlisted",
                detail={"allowed": sorted(ctx.config.allowed_commands)},
            )
        argv = entry.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(part, str) for part in argv):
            raise ToolUnavailable("allowlisted command is misconfigured")
        return entry

    def risk_for(self, args: RunAllowedInput, ctx: ToolContext | None = None) -> Risk:
        if ctx is None:
            return self.risk
        try:
            entry = self._entry(ctx, args.command_id)
        except Exception:
            # Unknown command IDs are refused at execution, not escalated into an
            # approval prompt.
            return self.risk
        try:
            return Risk(str(entry.get("risk", "medium")))
        except ValueError:
            return Risk.MEDIUM

    def run_sync(self, args: RunAllowedInput, ctx: ToolContext) -> dict[str, Any]:
        entry = self._entry(ctx, args.command_id)
        argv = list(entry["argv"])
        timeout = min(float(entry.get("timeout", 60)), self.timeout_seconds)
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, shell disabled
                argv,
                cwd=str(ctx.config.workspace_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                env=safe_environment(),
                creationflags=creation_flags,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolExecutionError(f"command timed out after {timeout:.0f}s") from exc
        except FileNotFoundError as exc:
            raise ToolUnavailable(f"command not available on this machine: {argv[0]}") from exc
        return {
            "command_id": args.command_id,
            "argv": argv,
            "exit_code": completed.returncode,
            "stdout": self.truncate(completed.stdout or ""),
            "stderr": self.truncate(completed.stderr or ""),
            "timed_out": False,
        }

    def verify(self, args: RunAllowedInput, output: dict[str, Any]) -> VerificationEvidence:
        exit_code = output.get("exit_code")
        completed = isinstance(exit_code, int) and not output.get("timed_out")
        ok = completed and exit_code == 0
        return VerificationEvidence(
            ok=ok,
            confidence=0.85 if ok else 0.0,
            checks=["exit_code_zero" if ok else f"exit_code_{exit_code}"],
            detail={"exit_code": exit_code, "command_id": output.get("command_id")},
        )
