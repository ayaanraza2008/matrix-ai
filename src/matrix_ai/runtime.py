"""Composition root: one wired runtime shared by the API and the CLI."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import __version__
from .config import MatrixConfig
from .core.models import (
    DEFAULT_SCOPES,
    AutonomyLevel,
    HealthView,
    Principal,
    ToolSpec,
    iso,
)
from .core.orchestrator import Orchestrator
from .core.policy import PolicyEngine
from .core.worker_pool import AgentWorkerPool
from .errors import PermissionDenied
from .integrations.addons import AddonManager
from .integrations.registry import IntegrationRegistry
from .integrations.youtube_automation import build_youtube_tools
from .memory.service import MemoryService
from .models.manager import ModelManager
from .models.planner import PlannerService
from .scheduler import Scheduler
from .security.auth import AuthService
from .security.ratelimit import RateLimiter
from .storage.db import Database
from .storage.store import Store
from .tools.base import ToolContext
from .tools.registry import ToolRegistry, build_registry
from .vision.manager import VisionSessionManager
from .voice.manager import TtsEngine, VoiceSessionManager


@dataclass
class MatrixRuntime:
    config: MatrixConfig
    db: Database
    store: Store
    registry: ToolRegistry
    policy: PolicyEngine
    models: ModelManager
    memory: MemoryService
    planner: PlannerService
    worker_pool: AgentWorkerPool
    orchestrator: Orchestrator
    auth: AuthService
    limiter: RateLimiter
    voice: VoiceSessionManager
    vision: VisionSessionManager
    integrations: IntegrationRegistry
    addon_manager: AddonManager
    scheduler: Scheduler
    started_at: float = field(default_factory=time.time)
    version: str = __version__

    # -- construction -----------------------------------------------------
    @classmethod
    def build(cls, config: MatrixConfig | None = None, *, tts_engine: TtsEngine | None = None) -> MatrixRuntime:
        config = config or MatrixConfig.from_env()
        db = Database(config.db_path)
        store = Store(db)
        registry = build_registry()
        for youtube_tool in build_youtube_tools(config):
            registry.register(youtube_tool)
        policy = PolicyEngine(config, store)
        models = ModelManager(config, store)
        memory = MemoryService(db)
        addon_manager = AddonManager(config, store=store)
        worker_pool = AgentWorkerPool(
            models,
            registry,
            max_concurrent=config.agent_max_concurrent_workers,
            timeout_seconds=config.agent_worker_timeout_seconds,
        )
        planner = PlannerService(
            registry, models, store, worker_pool=worker_pool, addon_manager=addon_manager
        )
        orchestrator = Orchestrator(
            config=config,
            store=store,
            registry=registry,
            policy=policy,
            models=models,
            planner=planner,
            memory=memory,
        )
        auth = AuthService(store, config)
        auth.bootstrap()
        runtime = cls(
            config=config,
            db=db,
            store=store,
            registry=registry,
            policy=policy,
            models=models,
            memory=memory,
            planner=planner,
            worker_pool=worker_pool,
            orchestrator=orchestrator,
            auth=auth,
            limiter=RateLimiter(config.rate_limit_per_minute),
            voice=VoiceSessionManager(config, tts_engine),
            vision=VisionSessionManager(config),
            integrations=IntegrationRegistry(config, store),
            addon_manager=addon_manager,
            scheduler=Scheduler(
                store=store, orchestrator=orchestrator, models=models, memory=memory, config=config
            ),
        )
        store.audit(
            "system.started",
            actor="system",
            status="ok",
            detail={"version": runtime.version, "loopback_only": config.is_loopback()},
        )
        return runtime

    # -- safe read-only tool use (dashboard) ------------------------------
    async def readonly_tool_call(
        self, tool_name: str, arguments: dict[str, Any], *, principal: Principal | None = None
    ) -> dict[str, Any]:
        tool = self.registry.get(tool_name)
        validated = tool.validate(arguments)
        ctx = ToolContext(
            task_id="readonly",
            config=self.config,
            user_id=principal.user_id if principal else "owner",
            scopes=list(DEFAULT_SCOPES),
            autonomy_level=int(AutonomyLevel.SAFE_EXECUTION),
            store=self.store,
            extras={"memory": self.memory},
        )
        decision = self.policy.evaluate_call(
            tool.spec(),
            scopes=list(DEFAULT_SCOPES),
            autonomy=int(AutonomyLevel.SAFE_EXECUTION),
            risk=tool.risk_for(validated, ctx),
        )
        if not decision.allowed or decision.requires_approval:
            raise PermissionDenied("this tool cannot be used for dashboard reads", detail=decision.to_dict())
        output = await asyncio.wait_for(tool.run(validated, ctx), timeout=tool.timeout_seconds)
        self.store.audit(
            "tool.readonly_executed",
            actor=ctx.user_id,
            tool_name=tool_name,
            status="succeeded",
            detail={"keys": sorted(output)[:20]},
        )
        return output

    def tool_catalog(self) -> list[ToolSpec]:
        return self.registry.catalog()

    # -- health -----------------------------------------------------------
    async def health(self) -> HealthView:
        database = "ok" if self.db.integrity_check() == "ok" else "degraded"
        model = await self.models.status()
        checks = {
            "database": database,
            "model": model.status,
            "voice": self.voice.status().stt_provider,
            "vision": "ocr" if self.vision.ocr_available else "capture_only",
            "integrations": f"{self.integrations.configured_count}/{self.integrations.total_count}",
            "addons": "available",
            "emergency_stop": "active" if self.store.emergency_stop else "off",
        }
        degraded = database != "ok" or self.store.emergency_stop or model.status in {"unavailable", "degraded"}
        return HealthView(
            status="degraded" if degraded else "ok",
            version=self.version,
            uptime_seconds=round(time.time() - self.started_at, 1),
            started_at=iso(datetime.fromtimestamp(self.started_at, UTC)),
            database=database,
            model=model,
            integrations_configured=self.integrations.configured_count,
            integrations_total=self.integrations.total_count,
            emergency_stop=self.store.emergency_stop,
            checks=checks,
        )

    # -- lifecycle --------------------------------------------------------
    def start_background(self) -> None:
        self.scheduler.start()

    async def shutdown(self) -> None:
        await self.scheduler.stop()
        await self.voice.shutdown()
        self.vision.stop_all()
        await self.orchestrator.shutdown()
        await self.worker_pool.shutdown()
        await self.addon_manager.disconnect_all(self.registry)
        self.store.audit("system.stopped", actor="system", status="ok")
        self.db.close()
