from __future__ import annotations

from matrix_ai.integrations.session_adapters import normalize_agent_record


def test_claude_code_tool_use_adapter_discards_prompt_and_arguments():
    fmt, events = normalize_agent_record({
        "type": "assistant",
        "message": {"content": [
            {"type": "text", "text": "PRIVATE PROMPT"},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/private/file"}},
        ]},
    })
    assert fmt == "claude_code"
    assert events == [{
        "type": "tool_call", "tool_name": "Read", "is_error": False, "role": "assistant"
    }]
    assert "PRIVATE PROMPT" not in str(events)
    assert "/private/file" not in str(events)


def test_claude_code_tool_result_adapter_keeps_only_error_flag():
    fmt, events = normalize_agent_record({
        "type": "user",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": "secret output"}
        ]},
    })
    assert fmt == "claude_code"
    assert events[0]["is_error"] is True
    assert "secret output" not in str(events)


def test_codex_function_call_adapter_keeps_only_tool_name():
    fmt, events = normalize_agent_record({
        "type": "response_item",
        "payload": {
            "type": "function_call", "name": "shell", "arguments": '{"command":"secret"}',
        },
    })
    assert fmt == "codex"
    assert events[0]["tool_name"] == "shell"
    assert "secret" not in str(events)


def test_generic_normalized_jsonl_is_preserved_for_existing_analyzer():
    record = {"type": "tool_call", "tool_name": "search", "prompt": "private"}
    fmt, events = normalize_agent_record(record)
    assert fmt == "normalized_jsonl"
    assert events == [record]


def test_codex_output_correlates_to_original_function_call_name():
    names = {}
    call_format, call_events = normalize_agent_record({
        "type": "response_item",
        "payload": {"type": "function_call", "call_id": "call-42", "name": "shell"},
    }, names)
    output_format, output_events = normalize_agent_record({
        "type": "response_item",
        "payload": {"type": "function_call_output", "call_id": "call-42", "is_error": True},
    }, names)
    assert call_format == output_format == "codex"
    assert call_events[0]["tool_name"] == "shell"
    assert output_events[0]["tool_name"] == "shell"
    assert output_events[0]["is_error"] is True
