"""Secret detection and redaction.

Two related jobs:

1. ``redact_text`` / ``redact_obj`` scrub audit records, logs and API payloads.
2. ``contains_secret`` blocks credentials from ever entering semantic memory.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "bot_token",
    "client_secret",
    "device_key",
    "key",
    "owner_token",
    "passwd",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session_token",
    "token",
    "access_token",
}

_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}\b"),  # Telegram bot token shape
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]{0,4000}?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\b\s*[:=]\s*['\"]?[A-Za-z0-9._\-/+]{8,}"),
)

_SURROGATE = "\u0000"


def redact_text(text: str, *, max_length: int = 4000) -> str:
    if not isinstance(text, str):
        text = str(text)
    cleaned = text
    for pattern in _VALUE_PATTERNS:
        cleaned = pattern.sub(REDACTED, cleaned)
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length] + f"...[{len(cleaned) - max_length} chars truncated]"
    return cleaned


def contains_secret(text: str) -> bool:
    if not isinstance(text, str):
        return False
    return any(pattern.search(text) for pattern in _VALUE_PATTERNS)


def redact_obj(value: Any, *, depth: int = 0, max_items: int = 60) -> Any:
    """Recursively redact a JSON-compatible object with bounded depth/size."""
    if depth > 6:
        return "[depth-limit]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                result["_truncated"] = True
                break
            lowered = str(key).lower()
            if lowered in SENSITIVE_KEYS or any(token in lowered for token in ("token", "secret", "password")):
                result[str(key)] = REDACTED if item not in (None, "", False) else item
            else:
                result[str(key)] = redact_obj(item, depth=depth + 1, max_items=max_items)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_obj(item, depth=depth + 1, max_items=max_items) for item in value[:max_items]]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Redact tool arguments for approval previews and audit trails."""
    result = redact_obj(arguments)
    return result if isinstance(result, dict) else {}
