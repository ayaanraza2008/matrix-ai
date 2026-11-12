"""Integration adapter contract.

An integration is optional by definition. Missing credentials produce an honest
"missing_credentials" status; a broken integration must never crash the core
assistant, and a provider is never reported as having performed an action it did
not confirm.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from ..config import MatrixConfig
from ..core.models import IntegrationView

STATUS_OK = "working"
STATUS_CONFIGURED = "configured"
STATUS_MISSING = "missing_credentials"
STATUS_DENIED = "permission_denied"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_DEGRADED = "degraded"
STATUS_UNAVAILABLE = "unavailable"


@dataclass
class IntegrationResult:
    status: str
    detail: str | None = None


class Integration(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    category: ClassVar[str] = "optional"
    required_settings: ClassVar[tuple[str, ...]] = ()

    def __init__(self, config: MatrixConfig) -> None:
        self.config = config

    @property
    def configured(self) -> bool:  # pragma: no cover - overridden
        return False

    @abstractmethod
    async def check(self) -> IntegrationResult: ...

    async def safe_check(self) -> IntegrationResult:
        if not self.configured:
            settings = ", ".join(self.required_settings) or "credentials"
            return IntegrationResult(STATUS_MISSING, f"Not configured. Required: {settings}.")
        try:
            return await self.check()
        except Exception as exc:  # noqa: BLE001 - integration boundary must not crash
            return IntegrationResult(STATUS_UNAVAILABLE, f"{type(exc).__name__} while checking the provider.")

    def view(self, stored) -> IntegrationView:
        status = STATUS_CONFIGURED if self.configured else STATUS_MISSING
        detail = None
        checked_at = None
        if stored is not None:
            status = stored["status"]
            detail = stored["detail"]
            checked_at = stored["checked_at"]
        elif not self.configured:
            settings = ", ".join(self.required_settings) or "credentials"
            detail = f"Not configured. Required: {settings}."
        return IntegrationView(
            name=self.name,
            description=self.description,
            category=self.category,
            status=status,
            detail=detail,
            required_settings=list(self.required_settings),
            checked_at=checked_at,
        )
