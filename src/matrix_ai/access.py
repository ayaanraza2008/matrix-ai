"""Secure private remote access and per-user startup helpers.

Remote access is deliberately limited to Tailscale Serve (tailnet-only HTTPS)
and a loopback MATRIX listener. This module never configures Tailscale Funnel,
never binds MATRIX to 0.0.0.0, and never accepts arbitrary CLI arguments.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REMOTE_TARGET = "127.0.0.1:8765"
STARTUP_TASK_NAME = "MATRIX AI"


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _run(argv: list[str], *, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    """Run a fixed-argument local helper; shell execution is never enabled."""
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, shell=False, creationflags=_creationflags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Local helper timed out after {timeout:g} seconds.") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not start local helper ({type(exc).__name__}).") from exc


class RemoteAccessManager:
    """Manage an HTTPS reverse proxy available only to the configured tailnet."""

    def __init__(self, executable: str | None = None) -> None:
        configured = os.getenv("MATRIX_TAILSCALE_BIN")
        self.executable = executable or configured or shutil.which("tailscale")
        if not self.executable and sys.platform == "win32":
            candidates = [
                Path(os.getenv("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe",
                Path(os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tailscale" / "tailscale.exe",
            ]
            self.executable = next((str(path) for path in candidates if path.is_file()), None)

    def _call(self, *args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
        if not self.executable:
            raise RuntimeError(
                "Tailscale CLI was not found. Install Tailscale and sign this PC into your private tailnet."
            )
        result = _run([self.executable, *args], timeout=timeout)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Tailscale command failed.").strip()
            raise RuntimeError(detail[-1200:])
        return result

    @staticmethod
    def _endpoint(text: str) -> str | None:
        match = re.search(r"https://[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net(?::\d+)?", text, re.I)
        return match.group(0) if match else None

    def _serve_status(self) -> str:
        if not self.executable:
            raise RuntimeError("Tailscale CLI was not found.")
        result = _run([self.executable, "serve", "status"])
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 and not re.search(r"no .*serve|serve .*not configured", output, re.I):
            raise RuntimeError((output or "Tailscale Serve status failed.").strip()[-1200:])
        return output

    def status(self) -> dict[str, Any]:
        if not self.executable:
            return {
                "installed": False, "connected": False, "serve_enabled": False,
                "endpoint": None, "scope": "tailnet_only",
                "public_funnel_enabled_by_matrix": False,
                "detail": "Tailscale is not installed or its CLI is not on PATH.",
            }
        try:
            node = self._call("status", "--json")
            node_data = json.loads(node.stdout or "{}")
            self_data = node_data.get("Self") or {}
            backend_state = str(node_data.get("BackendState", ""))
            serve_text = self._serve_status()
            active = REMOTE_TARGET in serve_text
            endpoint = self._endpoint(serve_text)
            online = backend_state.lower() == "running" and bool(self_data)
            return {
                "installed": True, "connected": online,
                "hostname": self_data.get("HostName"),
                "serve_enabled": active, "endpoint": endpoint if active else None,
                "scope": "tailnet_only", "public_funnel_enabled_by_matrix": False,
                "detail": (
                    "MATRIX is available only to devices allowed by your Tailscale ACLs."
                    if active and online else
                    "Tailscale is connected but MATRIX Serve is not enabled."
                    if online else
                    "Tailscale is installed but this PC is not connected to a tailnet."
                ),
            }
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
            return {
                "installed": True, "connected": False, "serve_enabled": False,
                "endpoint": None, "scope": "tailnet_only",
                "public_funnel_enabled_by_matrix": False,
                "detail": f"Tailscale status unavailable ({type(exc).__name__}).",
            }

    def enable(self) -> dict[str, Any]:
        state = self.status()
        if not state["installed"] or not state["connected"]:
            raise RuntimeError("Connect this PC to Tailscale first; MATRIX will not create a public tunnel.")
        if state["serve_enabled"]:
            return state
        current = self._serve_status()
        no_config = not current.strip() or bool(re.search(r"no .*serve|serve .*not configured", current, re.I))
        if current.strip() and not no_config and REMOTE_TARGET not in current:
            raise RuntimeError(
                "Another Tailscale Serve route is already configured. Review it manually before enabling MATRIX."
            )
        result = self._call("serve", "--bg", REMOTE_TARGET)
        updated = self.status()
        if not updated["serve_enabled"]:
            updated["detail"] = (result.stdout or "").strip()[-800:] or "Serve command completed; status is not yet visible."
        return updated

    def disable(self) -> dict[str, Any]:
        state = self.status()
        if not state["installed"] or not state["serve_enabled"]:
            return state
        self._call("serve", "--https=443", REMOTE_TARGET, "off")
        return self.status()


class StartupTaskManager:
    """Install MATRIX to start at the current Windows user's interactive logon."""

    def __init__(self, task_name: str = STARTUP_TASK_NAME) -> None:
        self.task_name = task_name

    def _require_windows(self) -> str:
        if sys.platform != "win32":
            raise RuntimeError("Automatic startup task installation is currently supported on Windows only.")
        executable = shutil.which("schtasks.exe") or shutil.which("schtasks")
        if not executable:
            raise RuntimeError("Windows Task Scheduler CLI (schtasks.exe) was not found.")
        return executable

    def status(self) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"supported": False, "installed": False, "detail": "Windows startup task only."}
        executable = shutil.which("schtasks.exe") or shutil.which("schtasks")
        if not executable:
            return {"supported": True, "installed": False, "detail": "schtasks.exe was not found."}
        result = _run([executable, "/Query", "/TN", self.task_name, "/FO", "LIST"])
        if result.returncode != 0:
            return {"supported": True, "installed": False, "detail": "MATRIX logon task is not installed."}
        return {"supported": True, "installed": True, "detail": "MATRIX is configured to start at interactive logon."}

    def install(self) -> dict[str, Any]:
        executable = self._require_windows()
        action = subprocess.list2cmdline([sys.executable, "-m", "matrix_ai", "serve"])
        result = _run([
            executable, "/Create", "/TN", self.task_name, "/SC", "ONLOGON",
            "/TR", action, "/RL", "LIMITED", "/IT", "/F",
        ], timeout=30)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Task Scheduler rejected the task.").strip()
            raise RuntimeError(detail[-1200:])
        return {"supported": True, "installed": True, "detail": "MATRIX will start when this Windows user logs on."}

    def uninstall(self) -> dict[str, Any]:
        executable = self._require_windows()
        result = _run([executable, "/Delete", "/TN", self.task_name, "/F"], timeout=30)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Task Scheduler could not remove the task.").strip()
            if "cannot find" not in detail.lower() and "does not exist" not in detail.lower():
                raise RuntimeError(detail[-1200:])
        return {"supported": True, "installed": False, "detail": "MATRIX logon startup task is removed."}
