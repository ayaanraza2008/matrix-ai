"""Typed error hierarchy shared by the API, orchestrator and tools.

Every error carries an HTTP status and a stable machine-readable ``code`` so the
UI and tests can assert on behaviour instead of parsing prose.
"""

from __future__ import annotations


class MatrixError(Exception):
    """Base class for all expected MATRIX failures."""

    status_code = 500
    code = "matrix_error"

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class ConfigError(MatrixError):
    status_code = 500
    code = "config_error"


class ValidationFailure(MatrixError):
    status_code = 422
    code = "validation_failed"


class NotFoundError(MatrixError):
    status_code = 404
    code = "not_found"


class ConflictError(MatrixError):
    status_code = 409
    code = "conflict"


class AuthenticationError(MatrixError):
    status_code = 401
    code = "unauthenticated"


class PermissionDenied(MatrixError):
    """Policy or scope refusal. Never a UI-only check."""

    status_code = 403
    code = "permission_denied"


class RateLimited(MatrixError):
    status_code = 429
    code = "rate_limited"


class IllegalTransition(MatrixError):
    status_code = 409
    code = "illegal_transition"


class ToolNotFound(MatrixError):
    status_code = 404
    code = "tool_not_found"


class ToolUnavailable(MatrixError):
    """Dependency, OS capability or optional package is missing."""

    status_code = 503
    code = "tool_unavailable"


class ToolExecutionError(MatrixError):
    status_code = 500
    code = "tool_execution_failed"


class ModelUnavailable(MatrixError):
    """No local/cloud model answered. Callers must degrade honestly."""

    status_code = 503
    code = "model_unavailable"


class ApprovalRequired(MatrixError):
    status_code = 202
    code = "approval_required"


class EmergencyStopActive(PermissionDenied):
    code = "emergency_stop_active"


class VisionPermissionDenied(PermissionDenied):
    code = "camera_permission_denied"


class VoiceMuted(ConflictError):
    code = "microphone_muted"


class IntegrationUnavailable(MatrixError):
    status_code = 503
    code = "integration_unavailable"


class OperationCancelled(MatrixError):
    """Raised cooperatively when a task is cancelled mid-tool."""

    status_code = 409
    code = "cancelled"
