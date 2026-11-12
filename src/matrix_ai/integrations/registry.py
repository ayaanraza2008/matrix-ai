"""Integration registry with cached health status."""

from __future__ import annotations

from ..config import MatrixConfig
from ..core.models import IntegrationView
from ..storage.store import Store
from .adapters import build_integrations
from .base import Integration


class IntegrationRegistry:
    def __init__(self, config: MatrixConfig, store: Store) -> None:
        self.store = store
        self.integrations: dict[str, Integration] = {
            integration.name: integration for integration in build_integrations(config)
        }

    def views(self) -> list[IntegrationView]:
        stored = self.store.integration_rows()
        views = [
            integration.view(stored.get(integration.name))
            for integration in self.integrations.values()
        ]
        return sorted(views, key=lambda view: (view.category, view.name))

    def get(self, name: str) -> Integration:
        from ..errors import NotFoundError

        integration = self.integrations.get(name)
        if integration is None:
            raise NotFoundError(f"integration '{name}' is not known")
        return integration

    async def check(self, name: str) -> IntegrationView:
        integration = self.get(name)
        result = await integration.safe_check()
        self.store.upsert_integration(name, result.status, result.detail)
        return integration.view(self.store.integration_rows().get(name))

    async def check_all(self) -> list[IntegrationView]:
        for name in list(self.integrations):
            try:
                await self.check(name)
            except Exception:  # noqa: BLE001 - one bad adapter must not break the rest
                continue
        return self.views()

    @property
    def configured_count(self) -> int:
        return sum(1 for integration in self.integrations.values() if integration.configured)

    @property
    def total_count(self) -> int:
        return len(self.integrations)
