"""Read-only system telemetry (psutil)."""

from __future__ import annotations

import platform
import time
from pathlib import Path
from typing import Any

import psutil

from ..core.models import Risk, VerificationEvidence
from .base import StrictModel, Tool, ToolContext


class SystemStatusInput(StrictModel):
    include_top_processes: bool = False


class SystemStatusTool(Tool):
    name = "system.get_status"
    description = "Read CPU, memory, disk, battery and uptime metrics for this PC."
    permission_scope = "system.read"
    risk = Risk.LOW
    idempotent = True
    timeout_seconds = 15.0
    input_model = SystemStatusInput

    def run_sync(self, args: SystemStatusInput, ctx: ToolContext) -> dict[str, Any]:
        memory = psutil.virtual_memory()
        disk_anchor = str(Path(ctx.config.workspace_dir).anchor or "/")
        disk = psutil.disk_usage(disk_anchor)
        battery = None
        try:
            battery_info = psutil.sensors_battery()
            if battery_info is not None:
                battery = {"percent": battery_info.percent, "plugged": battery_info.power_plugged}
        except (AttributeError, NotImplementedError):  # pragma: no cover - platform dependent
            battery = None

        output: dict[str, Any] = {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu": {
                "physical_cores": psutil.cpu_count(logical=False),
                "logical_cores": psutil.cpu_count(logical=True),
                "percent": psutil.cpu_percent(interval=0.1),
                "load_average": list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else None,
            },
            "memory": {
                "total_mb": round(memory.total / 1024**2, 1),
                "available_mb": round(memory.available / 1024**2, 1),
                "used_percent": memory.percent,
            },
            "disk": {
                "total_gb": round(disk.total / 1024**3, 2),
                "free_gb": round(disk.free / 1024**3, 2),
                "used_percent": disk.percent,
            },
            "battery": battery,
            "boot_time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(psutil.boot_time())),
            "uptime_seconds": round(time.time() - psutil.boot_time(), 1),
            "model_ram_budget_mb": ctx.config.model_ram_budget_mb,
        }
        if args.include_top_processes:
            processes = []
            for process in psutil.process_iter(["pid", "name", "memory_info"]):
                try:
                    info = process.info
                    processes.append(
                        {
                            "pid": info["pid"],
                            "name": (info["name"] or "")[:80],
                            "rss_mb": round((info["memory_info"].rss if info["memory_info"] else 0) / 1024**2, 1),
                        }
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            processes.sort(key=lambda item: item["rss_mb"], reverse=True)
            output["top_processes"] = processes[:5]
        return output

    def verify(self, args: SystemStatusInput, output: dict[str, Any]) -> VerificationEvidence:
        required = ("cpu", "memory", "disk", "uptime_seconds")
        missing = [key for key in required if key not in output]
        ok = not missing and isinstance(output.get("memory", {}).get("total_mb"), (int, float))
        return VerificationEvidence(
            ok=ok,
            confidence=0.95 if ok else 0.0,
            checks=["metrics_present" if ok else f"missing:{','.join(missing)}"],
            detail={"missing": missing, "memory_total_mb": output.get("memory", {}).get("total_mb")},
        )
