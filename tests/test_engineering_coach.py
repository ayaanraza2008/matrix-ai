from __future__ import annotations

import json

import pytest

from matrix_ai.errors import PermissionDenied, ValidationFailure
from matrix_ai.integrations.engineering_coach import (
    EngineeringCoachAnalyzeTool,
    analyze_jsonl,
)
from matrix_ai.tools.base import ToolContext


def _write_events(path):
    records = []
    for _ in range(4):
        records.extend([
            {"type": "tool_call", "tool_name": "search", "role": "assistant"},
            {"type": "tool_call", "tool_name": "summarize", "role": "assistant"},
        ])
    for _ in range(3):
        records.append({
            "type": "tool_result", "tool_name": "broken_tool",
            "is_error": True, "content": "PRIVATE PROMPT MUST NEVER BE RETURNED",
        })
    path.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")


def test_analyzer_returns_aggregate_metrics_and_skill_candidates(tmp_path):
    source = tmp_path / "session.jsonl"
    _write_events(source)
    result = analyze_jsonl(source)

    assert result["metrics"]["tool_calls"] == 11
    assert result["metrics"]["tool_errors"] == 3
    assert result["anti_patterns"][0]["id"] == "repeated_tool_failures"
    assert {"steps": ["search", "summarize"], "repetitions": 4} in result["skill_candidates"]
    assert result["privacy"]["raw_session_content_returned"] is False
    assert "PRIVATE PROMPT" not in str(result)


def test_analyzer_rejects_oversized_and_non_jsonl_sources(tmp_path):
    wrong = tmp_path / "session.txt"
    wrong.write_text("{}", encoding="utf-8")
    with pytest.raises(ValidationFailure, match="jsonl"):
        analyze_jsonl(wrong)


def test_tool_is_registered_and_limited_to_approved_roots(runtime):
    tool = runtime.registry.get("engineering_coach.analyze_session")
    assert isinstance(tool, EngineeringCoachAnalyzeTool)
    assert tool.permission_scope == "files.read"


def test_tool_rejects_paths_outside_approved_roots(config, tmp_path):
    source = tmp_path / "outside.jsonl"
    source.write_text('{"type":"tool_call","tool_name":"test"}', encoding="utf-8")
    tool = EngineeringCoachAnalyzeTool()
    args = tool.validate({"path": str(source)})
    ctx = ToolContext(task_id="test", config=config, scopes=["files.read"])
    with pytest.raises(PermissionDenied):
        tool.run_sync(args, ctx)


def test_tool_reports_no_raw_session_records(config):
    source = config.workspace_dir / "session.jsonl"
    source.write_text(
        '{"type":"tool_call","tool_name":"read_file","prompt":"sensitive text"}',
        encoding="utf-8",
    )
    tool = EngineeringCoachAnalyzeTool()
    args = tool.validate({"path": "session.jsonl"})
    ctx = ToolContext(task_id="test", config=config, scopes=["files.read"])
    output = tool.run_sync(args, ctx)
    assert output["metrics"]["tool_calls"] == 1
    assert "sensitive text" not in str(output)
    assert output["privacy"]["network_access"] is False


def test_analyzer_supports_claude_code_transcript_blocks(tmp_path):
    source = tmp_path / "claude.jsonl"
    records = []
    for _ in range(3):
        records.append({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "secret.txt"}},
                {"type": "tool_use", "name": "Edit", "input": {"new_string": "PRIVATE"}},
            ]},
        })
    source.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")
    result = analyze_jsonl(source)
    assert result["source"]["format"] == ["claude_code"]
    assert result["metrics"]["tool_calls"] == 6
    assert {"steps": ["Read", "Edit"], "repetitions": 3} in result["skill_candidates"]
    assert "PRIVATE" not in str(result)


async def test_engineering_coach_dashboard_api_requires_owner_and_reads_explicit_file(client, config):
    source = config.workspace_dir / "dashboard-session.jsonl"
    source.write_text(
        json.dumps({"type": "tool_call", "tool_name": "search"}), encoding="utf-8"
    )
    response = await client.post(
        "/api/engineering-coach/analyze", json={"path": "dashboard-session.jsonl"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["metrics"]["tool_calls"] == 1
    assert response.json()["privacy"]["raw_session_content_returned"] is False


async def test_engineering_coach_dashboard_api_rejects_unauthenticated(anon_client):
    response = await anon_client.post(
        "/api/engineering-coach/analyze", json={"path": "session.jsonl"}
    )
    assert response.status_code == 401
