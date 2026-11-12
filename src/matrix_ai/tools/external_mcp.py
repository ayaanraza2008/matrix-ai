"""Typed, policy-bound wrappers for owner-connected MCP tools."""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, create_model

from ..core.models import Risk, VerificationEvidence
from ..errors import ToolExecutionError, ValidationFailure
from .base import StrictModel, Tool, ToolContext


def _python_type(definition: dict[str, Any]) -> Any:
    schema_type = definition.get("type", "string")
    if isinstance(schema_type, list):
        non_null = [item for item in schema_type if item != "null"]
        schema_type = non_null[0] if non_null else "string"
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "object": dict[str, Any],
        "array": list[Any],
    }.get(schema_type, Any)


def _input_model(addon_id: str, tool: dict[str, Any]):
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    if not isinstance(properties, dict) or len(properties) > 64:
        raise ValidationFailure("MCP input schema is too large or invalid")
    fields: dict[str, tuple[Any, Any]] = {}
    for name, definition in properties.items():
        if not isinstance(name, str) or not name.isidentifier() or len(name) > 80 or not isinstance(definition, dict):
            raise ValidationFailure("MCP input schema contains an invalid field")
        python_type = _python_type(definition)
        description = str(definition.get("description") or "")[:300]
        if name in required:
            fields[name] = (python_type, Field(..., description=description))
        else:
            fields[name] = (python_type | None, Field(default=None, description=description))
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", f"{addon_id}_{tool['name']}")
    return create_model(f"MCP_{safe_name}_Input", __base__=StrictModel, **fields)


def build_mcp_tools(addon_id: str, definitions: list[dict[str, Any]], client) -> list[Tool]:
    created: list[Tool] = []
    for definition in definitions:
        name = str(definition.get("name") or "")
        if not name:
            continue
        annotations = definition.get("annotations") or {}
        read_only = annotations.get("readOnlyHint") is True
        risk = Risk.LOW if read_only else Risk.HIGH
        scope = "addons.read" if read_only else "addons.control"
        model = _input_model(addon_id, definition)
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)[:100]
        tool_name = f"addon.{addon_id}.{safe_name}"[:150]
        description = f"[{addon_id}] {str(definition.get('description') or name)[:700]}"

        class MCPTool(Tool):
            input_model = model
            risk = risk
            permission_scope = scope
            idempotent = bool(read_only)
            returns_untrusted = True
            timeout_seconds = 120.0
            requires_verification = True

            def run_sync(self, args, ctx):
                raise ToolExecutionError("MCP tools execute asynchronously through MATRIX")

            async def run(self, args, ctx: ToolContext) -> dict[str, Any]:
                ctx.ensure_active()
                return await client.call_tool(name, args.model_dump(mode="json", exclude_none=True))

            def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
                ok = isinstance(output, dict) and bool(output) and not output.get("isError")
                return VerificationEvidence(
                    ok=ok,
                    confidence=0.55 if ok else 0.0,
                    checks=["mcp_server_returned_result" if ok else "mcp_server_result_missing"],
                    detail={"server": addon_id, "tool": name, "postcondition": "not_independently_verified"},
                )

        MCPTool.__name__ = f"MCP_{addon_id}_{safe_name}"
        MCPTool.name = tool_name
        MCPTool.description = description
        created.append(MCPTool())
    return created


__all__ = ["build_mcp_tools"]
