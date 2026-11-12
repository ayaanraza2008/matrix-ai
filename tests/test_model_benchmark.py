"""Local model benchmark tests: validates structured plans without executing tools."""

from __future__ import annotations

import pytest

from matrix_ai.cli import _parser
from matrix_ai.models.base import ModelResponse
from matrix_ai.models.benchmark import BenchmarkCase, _percentile, run_model_benchmark
from matrix_ai.tools.registry import build_registry


class FakeModels:
    provider_name = "test-local"
    model_name = "test-model"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, messages, *, temperature=0.2, max_tokens=512, json_mode=False):
        self.calls += 1
        assert temperature == 0.0
        assert json_mode is True
        return ModelResponse(
            text=self.responses.pop(0),
            provider=self.provider_name,
            model=self.model_name,
            latency_ms=42.0,
        )

    def hardware(self):
        return {
            "total_ram_mb": 8192.0,
            "available_ram_mb": 4096.0,
            "budget_mb": 2500,
            "reserve_ram_mb": 2048,
            "profiles": [],
        }

    def fits_budget(self, ram_mb: int) -> bool:
        return ram_mb <= 2500


@pytest.mark.asyncio
async def test_benchmark_validates_plan_without_executing_tools():
    case = BenchmarkCase("status", "en", "Check my PC status.", "system.get_status")
    models = FakeModels(
        ['{"goal":"Check status","steps":[{"title":"Read status","kind":"tool",'
         '"tool_name":"system.get_status","arguments":{}}]}']
    )

    report = await run_model_benchmark(models, build_registry(), cases=(case,))

    assert report["schema_valid"] == 1
    assert report["tool_matches"] == 1
    assert report["tool_selection_accuracy_pct"] == 100.0
    assert report["latency_p50_ms"] == 42.0
    assert models.calls == 1
    assert report["safety_note"].startswith("Plans were parsed")
    assert report["results"][0]["actual_tool"] == "system.get_status"


@pytest.mark.asyncio
async def test_benchmark_counts_invalid_schema_and_wrong_tool_separately():
    cases = (
        BenchmarkCase("valid-wrong-tool", "en", "Check status.", "system.get_status"),
        BenchmarkCase("invalid", "hi", "स्टेटस बताओ।", "system.get_status"),
    )
    models = FakeModels(
        [
            '{"goal":"List files","steps":[{"title":"List","kind":"tool",'
            '"tool_name":"files.list","arguments":{"path":".","limit":10}}]}',
            "This is not JSON.",
        ]
    )

    report = await run_model_benchmark(models, build_registry(), cases=cases)

    assert report["schema_valid"] == 1
    assert report["tool_matches"] == 0
    assert report["schema_valid_rate_pct"] == 50.0
    assert report["tool_selection_accuracy_pct"] == 0.0
    assert report["results"][1]["schema_valid"] is False
    assert report["results"][1]["validation_error"]


def test_percentile_uses_nearest_rank_and_handles_no_samples():
    assert _percentile([], 50) is None
    assert _percentile([30.0, 10.0, 20.0], 50) == 20.0
    assert _percentile([30.0, 10.0, 20.0], 90) == 30.0


def test_cli_exposes_benchmark_model_command():
    args = _parser().parse_args(["benchmark-model"])
    assert args.command == "benchmark-model"
    assert args.max_cases == 8
    args = _parser().parse_args(["benchmark-model", "--max-cases", "3"])
    assert args.max_cases == 3
