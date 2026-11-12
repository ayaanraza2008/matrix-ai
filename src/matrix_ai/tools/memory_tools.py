"""Memory tools - the only way a plan may read or write governed memory."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ..core.models import MemoryKind, Risk, VerificationEvidence
from ..errors import ToolUnavailable
from ..memory.service import MemoryService
from .base import StrictModel, Tool, ToolContext


def _memory(ctx: ToolContext) -> MemoryService:
    service = ctx.extras.get("memory")
    if not isinstance(service, MemoryService):
        raise ToolUnavailable("memory service is not available in this context")
    return service


class MemorySearchInput(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    kinds: list[str] = Field(default_factory=list, max_length=5)
    limit: int = Field(default=5, ge=1, le=20)


class MemorySearchTool(Tool):
    name = "memory.search"
    description = "Search approved long-term memory with provenance."
    permission_scope = "memory.read"
    risk = Risk.LOW
    idempotent = True
    timeout_seconds = 10.0
    input_model = MemorySearchInput

    def run_sync(self, args: MemorySearchInput, ctx: ToolContext) -> dict[str, Any]:
        service = _memory(ctx)
        kinds = []
        for kind in args.kinds:
            try:
                kinds.append(MemoryKind(kind))
            except ValueError:
                continue
        items = service.search(args.query, kinds=kinds, limit=args.limit)
        return {"query": args.query, "items": items, "count": len(items)}

    def verify(self, args: MemorySearchInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = isinstance(output.get("items"), list)
        return VerificationEvidence(
            ok=ok,
            confidence=0.9 if ok else 0.0,
            checks=["retrieval_completed"],
            detail={"count": output.get("count", 0)},
        )


class MemorySaveInput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)
    kind: str = Field(default=MemoryKind.KNOWLEDGE.value, max_length=30)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class MemorySaveTool(Tool):
    name = "memory.save"
    description = "Store an approved user preference, project fact or note (never a secret)."
    permission_scope = "memory.write"
    risk = Risk.MEDIUM
    idempotent = False
    timeout_seconds = 10.0
    input_model = MemorySaveInput

    def run_sync(self, args: MemorySaveInput, ctx: ToolContext) -> dict[str, Any]:
        service = _memory(ctx)
        item = service.add(
            kind=args.kind,
            title=args.title,
            content=args.content,
            source=f"task:{ctx.task_id}",
            confidence=args.confidence,
        )
        return {"item": item.model_dump(mode="json")}

    def verify(self, args: MemorySaveInput, output: dict[str, Any]) -> VerificationEvidence:
        item = output.get("item") or {}
        ok = bool(item.get("id"))
        return VerificationEvidence(
            ok=ok,
            confidence=1.0 if ok else 0.0,
            checks=["memory_persisted" if ok else "memory_not_persisted"],
            detail={"id": item.get("id")},
        )
