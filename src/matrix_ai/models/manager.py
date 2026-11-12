"""Hardware-aware model manager.

On an 8 GB PC the rule is one primary model at a time, loaded on demand and
unloaded when idle. The manager reports honest status: unconfigured, unreachable,
degraded or working - never a fake "online".
"""

from __future__ import annotations

import time
from typing import Any

import psutil

from ..config import MatrixConfig
from ..core.models import ModelStatusView, iso, utcnow
from ..errors import ModelUnavailable
from .base import ChatMessage, ModelProvider, ModelResponse, ProviderHealth, build_provider

#: Declared approximate resident-memory footprints (quantised GGUF, CPU inference).
MODEL_PROFILES: tuple[dict[str, Any], ...] = (
    {"id": "qwen2.5:0.5b-instruct-q4_K_M", "ram_mb": 800, "role": "fast chat / tool routing"},
    {"id": "qwen2.5:1.5b-instruct-q4_K_M", "ram_mb": 1700, "role": "general chat"},
    {"id": "qwen2.5:3b-instruct-q4_K_M", "ram_mb": 2800, "role": "deeper reasoning"},
    {"id": "qwen2.5:7b-instruct-q4_K_M", "ram_mb": 5600, "role": "quality (16 GB+ only)"},
)

RESERVE_MB = 2048


class ModelManager:
    def __init__(self, config: MatrixConfig, store=None) -> None:
        self.config = config
        self.store = store
        self.provider: ModelProvider = build_provider(config)
        self._last_used_at: float | None = None
        self._assume_loaded = False
        self.last_error: str | None = None

    # -- capability -------------------------------------------------------
    @property
    def provider_name(self) -> str:
        return self.provider.provider_name

    @property
    def model_name(self) -> str:
        return self.provider.model

    @property
    def configured(self) -> bool:
        return self.config.model_provider != "none" and bool(self.config.model_name)

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 512,
        json_mode: bool = False,
    ) -> ModelResponse:
        response = await self.provider.complete(
            messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
        )
        self._last_used_at = time.monotonic()
        self._assume_loaded = True
        self.last_error = None
        if self.store is not None:
            self.store.audit(
                "model.completed",
                actor="model",
                status="ok",
                detail={
                    "provider": response.provider,
                    "model": response.model,
                    "latency_ms": round(response.latency_ms, 1),
                },
            )
        return response

    async def health(self) -> ProviderHealth:
        return await self.provider.health()

    async def unload(self) -> bool:
        """Release a locally loaded model when supported (Ollama keep_alive=0)."""
        if self.config.model_provider != "ollama":
            self._assume_loaded = False
            return False
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{self.config.ollama_url.rstrip('/')}/api/generate",
                    json={"model": self.config.model_name, "keep_alive": 0},
                )
        except httpx.HTTPError:
            return False
        finally:
            self._assume_loaded = False
        return True

    async def idle_check(self) -> bool:
        if not self._assume_loaded or self._last_used_at is None:
            return False
        if (time.monotonic() - self._last_used_at) >= self.config.model_idle_unload_s:
            return await self.unload()
        return False

    # -- hardware ---------------------------------------------------------
    def hardware(self) -> dict[str, Any]:
        memory = psutil.virtual_memory()
        total_mb = round(memory.total / 1024**2, 1)
        available_mb = round(memory.available / 1024**2, 1)
        usable_mb = max(0.0, available_mb - RESERVE_MB)
        recommended = None
        for profile in MODEL_PROFILES:
            if profile["ram_mb"] <= min(usable_mb, self.config.model_ram_budget_mb):
                recommended = profile
        return {
            "total_ram_mb": total_mb,
            "available_ram_mb": available_mb,
            "reserve_ram_mb": RESERVE_MB,
            "usable_for_model_mb": usable_mb,
            "logical_cores": psutil.cpu_count(logical=True),
            "physical_cores": psutil.cpu_count(logical=False),
            "budget_mb": self.config.model_ram_budget_mb,
            "recommended_profile": recommended,
            "profiles": list(MODEL_PROFILES),
            "concurrent_models": 1,
        }

    def fits_budget(self, ram_mb: int) -> bool:
        hardware = self.hardware()
        return ram_mb <= self.config.model_ram_budget_mb and ram_mb <= hardware["usable_for_model_mb"]

    # -- status -----------------------------------------------------------
    async def status(self) -> ModelStatusView:
        health = await self.health()
        if not self.configured:
            status = "unconfigured"
        elif health.status == "working":
            status = "working"
        elif health.reachable:
            status = "degraded"
        else:
            status = health.status or "unavailable"
        return ModelStatusView(
            provider=self.provider_name,
            model=self.model_name if self.configured else "none",
            configured=self.configured,
            reachable=health.reachable,
            status=status,
            detail=health.detail or self.last_error,
            ram_budget_mb=self.config.model_ram_budget_mb,
            loaded=self._assume_loaded,
            last_latency_ms=self.provider.last_latency_ms,
            last_checked=health.checked_at or iso(utcnow()),
        )


__all__ = ["MODEL_PROFILES", "ModelManager", "ModelUnavailable"]
