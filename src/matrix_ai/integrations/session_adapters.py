"""Format adapters for local coding-agent session exports.

Adapters intentionally emit only tool metadata. Prompt text, arguments, file
contents, and transcript payloads are discarded at the parsing boundary.
"""
from __future__ import annotations

from typing import Any


def _tool_event(name: Any, *, failed: bool = False, role: str = "assistant") -> dict[str, Any] | None:
    if not isinstance(name, str) or not name or len(name) > 100:
        return None
    return {
        "type": "tool_result" if failed else "tool_call",
        "tool_name": name,
        "is_error": bool(failed),
        "role": role,
    }


def normalize_agent_record(
    record: dict[str, Any], codex_tool_names: dict[str, str] | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """Return (format, metadata-only events); generic JSONL remains supported."""
    kind = str(record.get("type") or record.get("event") or "").casefold()
    events: list[dict[str, Any]] = []

    # Claude Code transcript JSONL: assistant message blocks contain tool_use;
    # user message blocks may contain tool_result with is_error.
    message = record.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        detected = "claude_code"
        for block in message["content"]:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "tool_use":
                event = _tool_event(block.get("name"))
            elif block_type == "tool_result":
                event = _tool_event(
                    block.get("name") or block.get("tool_name") or "unknown_tool",
                    failed=bool(block.get("is_error")),
                    role="tool",
                )
            else:
                event = None
            if event:
                events.append(event)
        return detected, events

    # Codex CLI rollout JSONL: response_item payloads carry function_call and
    # function_call_output records. Only the function name and error flag survive.
    payload = record.get("payload")
    if kind == "response_item" and isinstance(payload, dict):
        payload_type = str(payload.get("type") or "").casefold()
        if payload_type == "function_call":
            name = payload.get("name")
            call_id = payload.get("call_id") or payload.get("id")
            if isinstance(call_id, str) and isinstance(name, str) and codex_tool_names is not None:
                codex_tool_names[call_id] = name
            event = _tool_event(name)
            if event:
                events.append(event)
            return "codex", events
        if payload_type == "function_call_output":
            failed = bool(payload.get("is_error")) or str(payload.get("status", "")).casefold() in {
                "error", "failed", "failure"
            }
            call_id = payload.get("call_id") or payload.get("id")
            correlated_name = (
                codex_tool_names.get(call_id)
                if isinstance(call_id, str) and codex_tool_names is not None else None
            )
            event = _tool_event(
                payload.get("name") or payload.get("tool_name") or correlated_name,
                failed=failed,
                role="tool",
            )
            if event:
                events.append(event)
            return "codex", events

    # Already normalized / MATRIX-native JSONL.
    return "normalized_jsonl", [record]


SUPPORTED_SESSION_FORMATS = {
    "normalized_jsonl": "MATRIX normalized JSONL (type + tool_name)",
    "claude_code": "Claude Code transcript JSONL (tool_use/tool_result blocks)",
    "codex": "Codex CLI rollout JSONL (response_item function_call records)",
}
