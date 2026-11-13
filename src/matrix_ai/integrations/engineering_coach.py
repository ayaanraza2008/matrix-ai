"""Local, privacy-preserving engineering-session analytics for MATRIX.

The analyzer consumes only an explicitly selected JSONL file inside MATRIX's
configured filesystem roots. It returns aggregate metrics and recommendations,
never prompt text, tool arguments, paths, or raw session records.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import Field

from ..core.models import Risk
from ..errors import ValidationFailure
from ..tools.base import StrictModel, Tool, ToolContext

MAX_SESSION_BYTES = 10 * 1024 * 1024
MAX_SESSION_EVENTS = 50_000


def _event_kind(record: dict[str, Any]) -> str:
    value = record.get("type") or record.get("event") or record.get("role") or ""
    return str(value).casefold()


def _tool_name(record: dict[str, Any]) -> str | None:
    value = record.get("tool_name") or record.get("name")
    if isinstance(record.get("tool"), dict):
        value = value or record["tool"].get("name")
    if isinstance(value, str) and 0 < len(value) <= 100:
        return value
    return None


def _is_error(record: dict[str, Any]) -> bool:
    status = str(record.get("status") or "").casefold()
    return (
        record.get("is_error") is True
        or record.get("success") is False
        or bool(record.get("error"))
        or status in {"error", "failed", "failure"}
    )


def analyze_jsonl(path: Path) -> dict[str, Any]:
    """Analyze normalized JSONL events; raw contents are never returned or persisted."""
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file() or resolved.suffix.casefold() not in {".jsonl", ".ndjson"}:
            raise ValidationFailure("session source must be an existing .jsonl or .ndjson file")
        size = resolved.stat().st_size
        if size > MAX_SESSION_BYTES:
            raise ValidationFailure("session source exceeds the 10 MiB analysis limit")
        raw = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationFailure("session source must be UTF-8 JSONL") from exc
    except OSError as exc:
        raise ValidationFailure("session source could not be read") from exc

    events = 0
    malformed = 0
    tool_calls = 0
    tool_errors = 0
    tool_counts: Counter[str] = Counter()
    tool_successes: Counter[str] = Counter()
    sequence_counts: Counter[tuple[str, str]] = Counter()
    previous_successful_tool: str | None = None
    roles: Counter[str] = Counter()

    for line in raw.splitlines():
        if not line.strip():
            continue
        if events + malformed >= MAX_SESSION_EVENTS:
            break
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            malformed += 1
            continue
        if not isinstance(record, dict):
            malformed += 1
            continue
        events += 1
        kind = _event_kind(record)
        role = str(record.get("role") or kind).casefold()
        if role in {"user", "assistant", "system"}:
            roles[role] += 1

        name = _tool_name(record)
        is_tool_event = name is not None and (
            "tool" in kind or "function" in kind or "action" in kind
            or "tool_name" in record or isinstance(record.get("tool"), dict)
        )
        if not is_tool_event:
            continue
        tool_calls += 1
        tool_counts[name] += 1
        if _is_error(record):
            tool_errors += 1
            previous_successful_tool = None
        else:
            tool_successes[name] += 1
            if previous_successful_tool and previous_successful_tool != name:
                sequence_counts[(previous_successful_tool, name)] += 1
            previous_successful_tool = name

    failure_rate = round(tool_errors / tool_calls, 4) if tool_calls else 0.0
    anti_patterns: list[dict[str, Any]] = []
    recommendations: list[str] = []
    for name, count in tool_counts.most_common(10):
        failures = count - tool_successes[name]
        if failures >= 3:
            anti_patterns.append({
                "id": "repeated_tool_failures",
                "tool": name,
                "occurrences": failures,
                "severity": "high" if failures >= 5 else "medium",
            })
    if tool_calls >= 5 and failure_rate >= 0.2:
        anti_patterns.append({
            "id": "high_tool_failure_rate",
            "occurrences": tool_errors,
            "failure_rate": failure_rate,
            "severity": "high" if failure_rate >= 0.4 else "medium",
        })
        recommendations.append(
            "Investigate the most frequently failing tools and add argument/schema checks before retrying."
        )
    if not tool_calls:
        recommendations.append(
            "No recognizable tool events were found; normalize tool events with type='tool_call' and tool_name."
        )
    for (first, second), count in sequence_counts.most_common(5):
        if count >= 3:
            recommendations.append(
                f"Repeated workflow '{first} → {second}' appeared {count} times; consider packaging it as a reusable skill."
            )
    if tool_calls and failure_rate < 0.05:
        recommendations.append(
            "Tool execution reliability is strong in this sample; preserve the current validation and verification workflow."
        )
    if malformed:
        recommendations.append(
            f"{malformed} malformed JSONL line(s) were skipped; validate the export format before comparing sessions."
        )

    return {
        "schema_version": "matrix.engineering_coach.v1",
        "source": {"format": "jsonl", "bytes": size},
        "metrics": {
            "events_analyzed": events,
            "malformed_lines": malformed,
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "tool_failure_rate": failure_rate,
            "roles": dict(roles),
            "top_tools": [
                {"name": name, "calls": count, "successes": tool_successes[name]}
                for name, count in tool_counts.most_common(10)
            ],
        },
        "anti_patterns": anti_patterns,
        "skill_candidates": [
            {"steps": [first, second], "repetitions": count}
            for (first, second), count in sequence_counts.most_common(5)
            if count >= 3
        ],
        "recommendations": recommendations[:10],
        "privacy": {
            "raw_session_content_returned": False,
            "raw_session_content_persisted": False,
            "network_access": False,
        },
    }


class EngineeringCoachInput(StrictModel):
    path: str = Field(min_length=1, max_length=1024, description="Explicit .jsonl/.ndjson file inside an approved MATRIX filesystem root")


class EngineeringCoachAnalyzeTool(Tool):
    name = "engineering_coach.analyze_session"
    description = (
        "Analyze an explicitly selected local JSONL coding-agent session export. "
        "Returns aggregate reliability metrics, repeated failure patterns, reusable workflow candidates "
        "and recommendations. Never returns prompt text or raw records."
    )
    permission_scope = "files.read"
    risk = Risk.LOW
    timeout_seconds = 20.0
    idempotent = True
    returns_untrusted = False
    input_model = EngineeringCoachInput

    def run_sync(self, args: EngineeringCoachInput, ctx: ToolContext) -> dict[str, Any]:
        source = ctx.resolve_path(args.path, must_exist=True)
        if source.suffix.casefold() not in {".jsonl", ".ndjson"}:
            raise ValidationFailure("engineering coach accepts only .jsonl or .ndjson files")
        result = analyze_jsonl(source)
        result["source"]["approved_root"] = next(
            root.name for root in ctx.config.file_roots
            if source.is_relative_to(root)
        )
        return result
