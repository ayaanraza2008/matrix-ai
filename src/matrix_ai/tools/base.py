"""Typed tool contract.

Every tool must declare its permission scope, risk class, timeout, idempotency,
schema and verification strategy. There is no generic "run arbitrary code" or
"run this shell string" tool anywhere in MATRIX.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ..config import MatrixConfig
from ..core.models import AutonomyLevel, Risk, ToolSpec, VerificationEvidence
from ..errors import OperationCancelled, ToolExecutionError, ValidationFailure


class StrictModel(BaseModel):
    """Tool inputs reject unknown fields instead of silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


@dataclass
class ToolContext:
    task_id: str
    config: MatrixConfig
    user_id: str = "owner"
    device_id: str | None = None
    scopes: list[str] = field(default_factory=list)
    autonomy_level: int = 2
    cancel_event: asyncio.Event | None = None
    store: Any = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    def ensure_active(self) -> None:
        if self.cancelled:
            raise OperationCancelled("task cancelled by user")

    def resolve_path(self, raw: str, *, must_exist: bool = False) -> Path:
        """Resolve a user path inside the approved roots or refuse it."""
        if not isinstance(raw, str) or not raw.strip():
            raise ValidationFailure("path must be a non-empty string")
        if "\x00" in raw:
            raise ValidationFailure("path contains a null byte")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.config.workspace_dir / candidate
        try:
            resolved = candidate.resolve()
        except OSError as exc:  # pragma: no cover - platform dependent
            raise ValidationFailure(f"invalid path: {exc}") from exc
        if not self.config.in_allowed_root(resolved):
            from ..errors import PermissionDenied

            raise PermissionDenied(
                "path is outside the approved filesystem roots",
                detail={"path": str(resolved), "roots": [str(r) for r in self.config.file_roots]},
            )
        if must_exist and not resolved.exists():
            from ..errors import NotFoundError

            raise NotFoundError(f"path does not exist: {resolved.name}")
        return resolved


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    permission_scope: ClassVar[str]
    risk: ClassVar[Risk] = Risk.LOW
    autonomy_level: ClassVar[int] = int(AutonomyLevel.SAFE_EXECUTION)
    timeout_seconds: ClassVar[float] = 10.0
    idempotent: ClassVar[bool] = False
    returns_untrusted: ClassVar[bool] = False
    requires_verification: ClassVar[bool] = True
    max_output_bytes: ClassVar[int] = 100_000
    input_model: ClassVar[type[BaseModel]]

    # -- schema -----------------------------------------------------------
    @classmethod
    def spec(cls) -> ToolSpec:
        return ToolSpec(
            name=cls.name,
            description=cls.description,
            permission_scope=cls.permission_scope,
            risk=cls.risk,
            autonomy_level=int(cls.autonomy_level),
            timeout_seconds=float(cls.timeout_seconds),
            idempotent=bool(cls.idempotent),
            requires_verification=bool(cls.requires_verification),
            input_schema=cls.input_model.model_json_schema(),
        )

    def validate(self, raw_arguments: dict[str, Any]) -> BaseModel:
        if not isinstance(raw_arguments, dict):
            raise ValidationFailure(f"arguments for {self.name} must be an object")
        try:
            return self.input_model.model_validate(raw_arguments)
        except ValidationError as exc:
            raise ValidationFailure(
                f"invalid arguments for {self.name}",
                detail={"errors": exc.errors(include_url=False)},
            ) from exc

    # -- risk -------------------------------------------------------------
    def risk_for(self, args: BaseModel, ctx: ToolContext | None = None) -> Risk:
        """Risk may depend on arguments (e.g. overwrite vs create)."""
        return self.risk

    # -- execution --------------------------------------------------------
    @abstractmethod
    def run_sync(self, args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
        """Blocking implementation. Return a JSON-compatible dict."""

    async def run(self, args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
        ctx.ensure_active()
        return await asyncio.to_thread(self.run_sync, args, ctx)

    # -- verification -----------------------------------------------------
    def verify(self, args: BaseModel, output: dict[str, Any]) -> VerificationEvidence:
        ok = isinstance(output, dict) and bool(output)
        return VerificationEvidence(
            ok=ok,
            confidence=0.6 if ok else 0.0,
            checks=["tool_returned_output" if ok else "tool_returned_empty_output"],
        )

    # -- helpers ----------------------------------------------------------
    def truncate(self, text: str, limit: int | None = None) -> str:
        limit = limit or self.max_output_bytes
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def require_str(value: Any, field_name: str, *, max_length: int = 4000) -> str:
    if not isinstance(value, str) or not value:
        raise ToolExecutionError(f"{field_name} must be a non-empty string")
    return value[:max_length]
