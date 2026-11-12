"""Plan parsing and deterministic intent routing."""

from __future__ import annotations

import pytest

from matrix_ai.core.models import StepKind
from matrix_ai.errors import ValidationFailure
from matrix_ai.models.planner import (
    HeuristicPlanner,
    PlanParser,
    extract_json_object,
    render_tool_catalog,
)
from matrix_ai.tools.registry import build_registry


@pytest.fixture
def registry():
    return build_registry()


def test_tool_catalog_lists_declared_risks(registry):
    catalog = render_tool_catalog(registry)
    assert "system.get_status" in catalog
    assert "files.delete" in catalog
    assert "shell.run_allowed" in catalog
    assert "risk=high" in catalog


def test_extracts_json_from_markdown_fence():
    payload = extract_json_object('```json\n{"goal": "x", "steps": []}\n```')
    assert payload["goal"] == "x"


def test_extracts_json_from_chatty_output():
    payload = extract_json_object('Sure! {"goal": "y", "steps": []} hope that helps')
    assert payload["goal"] == "y"


def test_rejects_non_json():
    with pytest.raises(ValidationFailure):
        extract_json_object("I cannot plan that.")


def test_parser_accepts_valid_plan(registry):
    plan = PlanParser(registry).parse(
        '{"goal":"g","steps":[{"title":"status","kind":"tool",'
        '"tool_name":"system.get_status","arguments":{}}]}',
        fallback_goal="fallback",
    )
    assert plan.goal == "g"
    assert plan.source == "model"
    assert plan.steps[0].kind is StepKind.TOOL
    assert plan.steps[0].tool_name == "system.get_status"


def test_parser_rejects_unknown_tool(registry):
    with pytest.raises(ValidationFailure):
        PlanParser(registry).parse(
            '{"goal":"g","steps":[{"title":"x","kind":"tool","tool_name":"rm.everything",'
            '"arguments":{}}]}',
            fallback_goal="g",
        )


def test_parser_rejects_extra_arguments(registry):
    with pytest.raises(ValidationFailure):
        PlanParser(registry).parse(
            '{"goal":"g","steps":[{"title":"x","kind":"tool","tool_name":"files.list",'
            '"arguments":{"path":".","force":true}}]}',
            fallback_goal="g",
        )


def test_parser_rejects_too_many_steps(registry):
    steps = ",".join(
        '{"title":"s","kind":"tool","tool_name":"system.get_status","arguments":{}}'
        for _ in range(9)
    )
    with pytest.raises(ValidationFailure):
        PlanParser(registry).parse(f'{{"goal":"g","steps":[{steps}]}}', fallback_goal="g")


def test_parser_allows_answer_step(registry):
    plan = PlanParser(registry).parse(
        '{"goal":"g","steps":[{"title":"say","kind":"answer","answer":"hello"}]}',
        fallback_goal="g",
    )
    assert plan.steps[0].kind is StepKind.ANSWER
    assert plan.steps[0].arguments["answer"] == "hello"


def test_heuristic_routes_system_status(registry):
    intent = HeuristicPlanner(registry).plan("what is my system status?")
    assert intent.intent == "system_status"
    assert intent.plan.steps[0].tool_name == "system.get_status"


def test_heuristic_routes_file_write(registry):
    intent = HeuristicPlanner(registry).plan("create file 'notes.txt' with content 'hello'")
    assert intent.intent == "file_write"
    arguments = intent.plan.steps[0].arguments
    assert arguments["path"] == "notes.txt"
    assert arguments["content"] == "hello"


def test_heuristic_routes_memory_save(registry):
    intent = HeuristicPlanner(registry).plan("remember that the project uses SQLite")
    assert intent.intent == "memory_save"
    assert "SQLite" in intent.plan.steps[0].arguments["content"]


def test_heuristic_asks_for_clarification(registry):
    intent = HeuristicPlanner(registry).plan("delete that file")
    assert intent.intent == "needs_clarification"
    assert "which file" in intent.plan.steps[0].arguments["answer"].lower()


def test_heuristic_falls_back_to_chat(registry):
    intent = HeuristicPlanner(registry).plan("tell me a story about robots")
    assert intent.intent == "chat"
    assert intent.plan.steps[0].kind is StepKind.ANSWER
