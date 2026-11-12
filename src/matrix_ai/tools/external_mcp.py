"""Typed, policy-bound wrappers for owner-connected MCP tools."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

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


def _extract_targets(arguments: dict[str, Any]) -> set[str]:
    targets: set[str] = set()
    target_keys = {"target", "targets", "url", "host", "hostname", "domain", "ip", "ip_address", "target_url", "target_domain"}
    for key, value in arguments.items():
        if key.casefold() not in target_keys:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            if not isinstance(item, str):
                continue
            for part in item.split(","):
                raw = part.strip().casefold().rstrip(".")
                if not raw:
                    continue
                parsed = urlparse(raw if "://" in raw else "//" + raw)
                host = parsed.hostname or raw
                targets.add(host.casefold().rstrip("."))
    return targets


def _make_tool(
    addon_id: str, definition: dict[str, Any], client, read_only_tools: set[str], target_allowlist: set[str]
) -> Tool:
    external_name = str(definition.get("name") or "")
    if not external_name:
        raise ValidationFailure("MCP tool has no name")
    annotations = definition.get("annotations") or {}
    # A server annotation is not a permission grant. Owner configuration must
    # independently enumerate any tool trusted as read-only.
    read_only = (
        addon_id != "hexstrike-ai"
        and annotations.get("readOnlyHint") is True
        and external_name in read_only_tools
    )
    risk = Risk.LOW if read_only else Risk.HIGH
    scope = "addons.read" if read_only else "addons.control"
    model = _input_model(addon_id, definition)
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", external_name)[:100]
    tool_name = f"addon.{addon_id}.{safe_name}"[:150]
    description = f"[{addon_id}] {str(definition.get('description') or external_name)[:700]}"

    class MCPTool(Tool):
        input_model = model
        permission_scope = scope
        idempotent = bool(read_only)
        returns_untrusted = True
        timeout_seconds = 120.0
        requires_verification = True

        def run_sync(self, args, ctx):
            raise ToolExecutionError("MCP tools execute asynchronously through MATRIX")

        async def run(self, args, ctx: ToolContext) -> dict[str, Any]:
            ctx.ensure_active()
            if addon_id == "hexstrike-ai":
                targets = _extract_targets(args.model_dump(mode="json", exclude_none=True))
                blocked = sorted(target for target in targets if target not in target_allowlist)
                if blocked:
                    raise ToolExecutionError(
                        "HexStrike target is not in MATRIX_HEXSTRIKE_ALLOWED_TARGETS: " + ", ".join(blocked[:8])
                    )
            output = await client.call_tool(
                external_name, args.model_dump(mode="json", exclude_none=True)
            )
            # All external result text is untrusted, even if it does not match
            # the prompt-injection heuristic; later writes need fresh approval.
            output["suspicious"] = True
            return output

        def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
            ok = isinstance(output, dict) and bool(output) and not output.get("isError")
            return VerificationEvidence(
                ok=ok,
                confidence=0.55 if ok else 0.0,
                checks=["mcp_server_returned_response" if ok else "mcp_server_result_missing"],
                detail={
                    "server": addon_id,
                    "tool": external_name,
                    "postcondition": "not_independently_verified",
                },
            )

    MCPTool.risk = risk
    MCPTool.__name__ = f"MCP_{addon_id}_{safe_name}"
    MCPTool.name = tool_name
    MCPTool.description = description
    return MCPTool()


def build_mcp_tools(
    addon_id: str,
    definitions: list[dict[str, Any]],
    client,
    *,
    read_only_tools: set[str] | None = None,
    target_allowlist: set[str] | None = None,
) -> list[Tool]:
    allowlist = read_only_tools or set()
    created: list[Tool] = []
    for definition in definitions:
        if not isinstance(definition, dict):
            continue
        created.append(
            _make_tool(addon_id, definition, client, allowlist, target_allowlist or set())
        )
    return created


__all__ = ["build_mcp_tools"]
