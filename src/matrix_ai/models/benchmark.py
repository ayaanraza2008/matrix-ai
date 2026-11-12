"""Repeatable, local-only model benchmark for MATRIX planning quality and resource use."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psutil

from ..core.models import StepKind
from ..errors import ModelUnavailable, ValidationFailure
from ..tools.registry import ToolRegistry
from .base import ChatMessage
from .manager import ModelManager
from .planner import PROMPT_VERSION, PlanParser, build_system_prompt


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    language: str
    prompt: str
    expected_tool: str


DEFAULT_BENCHMARK_CASES: tuple[BenchmarkCase, ...] = (
    BenchmarkCase("en_status", "en", "Check my PC system status.", "system.get_status"),
    BenchmarkCase("en_files", "en", "List the files in my MATRIX workspace.", "files.list"),
    BenchmarkCase("en_memory", "en", "Search your memory for MATRIX architecture.", "memory.search"),
    BenchmarkCase("en_command", "en", "Run the allowlisted command git_log.", "shell.run_allowed"),
    BenchmarkCase("hi_status", "hi", "मेरे कंप्यूटर का सिस्टम स्टेटस बताओ।", "system.get_status"),
    BenchmarkCase("hi_files", "hi", "मेरे MATRIX workspace की files दिखाओ।", "files.list"),
    BenchmarkCase("hi_memory", "hi", "मेरी memory में MATRIX architecture खोजो।", "memory.search"),
    BenchmarkCase("hi_app", "hi", "नोटपैड खोलो।", "apps.open"),
)


def _sample_memory() -> tuple[float | None, float | None]:
    """Return this process RSS and combined RSS of visible Ollama processes."""
    try:
        own_rss = psutil.Process().memory_info().rss / 1024**2
    except (psutil.Error, OSError):
        own_rss = None

    ollama_rss = 0.0
    found_ollama = False
    try:
        for process in psutil.process_iter(["name", "memory_info"]):
            try:
                name = (process.info.get("name") or "").lower()
                info = process.info.get("memory_info")
                if "ollama" in name and info is not None:
                    ollama_rss += info.rss / 1024**2
                    found_ollama = True
            except (psutil.Error, OSError, AttributeError):
                continue
    except (psutil.Error, OSError):
        return own_rss, None

    return own_rss, ollama_rss if found_ollama else None


async def _watch_memory(stop: asyncio.Event, peaks: dict[str, float | None]) -> None:
    while True:
        own_rss, ollama_rss = _sample_memory()
        if own_rss is not None:
            peaks["matrix_process_peak_rss_mb"] = max(
                peaks["matrix_process_peak_rss_mb"] or 0.0, own_rss
            )
        if ollama_rss is not None:
            peaks["ollama_process_peak_rss_mb"] = max(
                peaks["ollama_process_peak_rss_mb"] or 0.0, ollama_rss
            )
        if stop.is_set():
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.2)
        except TimeoutError:
            continue


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil((percentile / 100) * len(ordered)) - 1)
    return round(ordered[index], 1)


async def run_model_benchmark(
    models: ModelManager,
    registry: ToolRegistry,
    *,
    cases: Sequence[BenchmarkCase] = DEFAULT_BENCHMARK_CASES,
    max_tokens: int = 700,
) -> dict[str, Any]:
    """Benchmark model proposals only; no tool in the returned plan is executed."""
    selected_cases = tuple(cases)
    if not selected_cases:
        raise ValueError("at least one benchmark case is required")

    parser = PlanParser(registry)
    system_prompt = build_system_prompt(registry)
    peaks: dict[str, float | None] = {
        "matrix_process_peak_rss_mb": None,
        "ollama_process_peak_rss_mb": None,
    }
    stop = asyncio.Event()
    monitor = asyncio.create_task(_watch_memory(stop, peaks), name="matrix-model-benchmark-memory")
    results: list[dict[str, Any]] = []
    latencies: list[float] = []

    try:
        for case in selected_cases:
            response = None
            validation_error = None
            request_error = None
            actual_tool = None
            schema_valid = False
            matched = False
            started = time.perf_counter()
            try:
                response = await models.complete(
                    [
                        ChatMessage(role="system", content=system_prompt),
                        ChatMessage(role="user", content=case.prompt),
                    ],
                    temperature=0.0,
                    max_tokens=max_tokens,
                    json_mode=True,
                )
                latencies.append(float(response.latency_ms))
                try:
                    plan = parser.parse(response.text, fallback_goal=case.prompt[:300])
                    schema_valid = True
                    tool_steps = [step for step in plan.steps if step.kind is StepKind.TOOL]
                    actual_tool = tool_steps[0].tool_name if tool_steps else None
                    matched = actual_tool == case.expected_tool
                except ValidationFailure as exc:
                    validation_error = str(exc)[:300]
            except ModelUnavailable as exc:
                request_error = str(exc)[:300]

            results.append(
                {
                    "case_id": case.case_id,
                    "language": case.language,
                    "expected_tool": case.expected_tool,
                    "actual_tool": actual_tool,
                    "schema_valid": schema_valid,
                    "tool_match": matched,
                    "model_latency_ms": round(float(response.latency_ms), 1) if response else None,
                    "wall_latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "validation_error": validation_error,
                    "request_error": request_error,
                }
            )
    finally:
        stop.set()
        await monitor

    hardware = models.hardware()
    valid_count = sum(1 for result in results if result["schema_valid"])
    match_count = sum(1 for result in results if result["tool_match"])
    expected_count = sum(1 for case in selected_cases if case.expected_tool)
    profile = next(
        (item for item in hardware.get("profiles", []) if item.get("id") == models.model_name),
        None,
    )
    estimated_ram_mb = profile.get("ram_mb") if profile else None
    fits_budget = models.fits_budget(estimated_ram_mb) if estimated_ram_mb is not None else None

    return {
        "provider": models.provider_name,
        "model": models.model_name,
        "prompt_version": PROMPT_VERSION,
        "samples": len(selected_cases),
        "schema_valid": valid_count,
        "schema_valid_rate_pct": round(valid_count / len(selected_cases) * 100, 1),
        "tool_matches": match_count,
        "tool_selection_accuracy_pct": round(match_count / expected_count * 100, 1) if expected_count else None,
        "latency_p50_ms": _percentile(latencies, 50),
        "latency_p90_ms": _percentile(latencies, 90),
        "memory": {
            **peaks,
            "total_ram_mb": hardware.get("total_ram_mb"),
            "available_ram_mb": hardware.get("available_ram_mb"),
            "model_ram_budget_mb": hardware.get("budget_mb"),
            "reserved_ram_mb": hardware.get("reserve_ram_mb"),
            "profile_estimated_ram_mb": estimated_ram_mb,
            "profile_fits_current_budget": fits_budget,
            "ollama_rss_note": (
                "Combined RSS sampled from visible Ollama processes; it may not include a remote "
                "server or shared GPU memory."
            ),
        },
        "results": results,
        "safety_note": "Plans were parsed and measured only; no proposed tool action was executed.",
    }


__all__ = ["BenchmarkCase", "DEFAULT_BENCHMARK_CASES", "run_model_benchmark"]
