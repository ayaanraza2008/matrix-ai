"""A03 - malformed or unexpected tool arguments are rejected."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.acceptance


async def test_unknown_tool_is_refused(client):
    response = await client.post(
        "/api/tools/definitely.not.a.tool/execute", json={"arguments": {}}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "tool_not_found"


async def test_unexpected_argument_is_rejected_before_execution(client, runtime):
    response = await client.post(
        "/api/tools/files.list/execute",
        json={"arguments": {"path": ".", "unexpected_field": "value"}},
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "validation_failed"
    assert "unexpected_field" in response.text
    # No task and no tool call was created.
    assert runtime.store.db.query_one("SELECT COUNT(*) AS c FROM tasks")["c"] == 0


async def test_wrong_argument_type_is_rejected(client):
    response = await client.post(
        "/api/tools/files.read_text/execute", json={"arguments": {"path": 12345}}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


async def test_plan_parser_rejects_extra_fields(runtime):
    """Model-proposed arguments are validated against the tool schema."""
    from matrix_ai.errors import ValidationFailure
    from matrix_ai.models.planner import PlanParser

    parser = PlanParser(runtime.registry)
    with pytest.raises(ValidationFailure):
        parser.parse(
            '{"goal":"x","steps":[{"title":"l","kind":"tool","tool_name":"files.list",'
            '"arguments":{"path":".","evil":true}}]}',
            fallback_goal="x",
        )
