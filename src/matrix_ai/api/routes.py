"""HTTP surface.

Routes authenticate, validate input and delegate. They never run shell commands,
never evaluate policy themselves and never fabricate provider confirmations.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field

from ..core.agents import agent_catalog, get_agency_agent, route_agency_agents, search_agency_agents
from ..core.models import (
    DEFAULT_SCOPES,
    AuditEventView,
    ChatRequest,
    ChatResponse,
    DeviceView,
    HealthView,
    IntegrationView,
    JobCreate,
    JobView,
    MemoryCreate,
    MemoryView,
    PairingCodeView,
    PairRequest,
    PairResponse,
    Principal,
    Risk,
    TaskView,
    ToolExecuteRequest,
    ToolSpec,
    VisionStatusView,
    VoiceStatusView,
)
from ..errors import AuthenticationError, NotFoundError
from ..models.planner import PROMPT_VERSION
from ..runtime import MatrixRuntime

router = APIRouter(prefix="/api")


def get_runtime(request: Request) -> MatrixRuntime:
    return request.app.state.matrix


RuntimeDep = Annotated[MatrixRuntime, Depends(get_runtime)]


async def require_principal(
    request: Request,
    runtime: RuntimeDep,
    authorization: str | None = Header(default=None),
) -> Principal:
    token: str | None = None
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
    if not token:
        raise AuthenticationError("Authorization: Bearer <token> header is required")
    principal = runtime.auth.verify_token(token)
    runtime.limiter.check(principal.token_id or "unknown")
    request.state.principal = principal
    return principal


PrincipalDep = Annotated[Principal, Depends(require_principal)]


class MemoryUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=20_000)


class VoiceStartBody(BaseModel):
    muted: bool = False


class VoiceTranscribeBody(BaseModel):
    audio_base64: str = Field(min_length=8, max_length=12_000_000)
    session_id: str | None = None


class VoiceSpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


class VisionStartBody(BaseModel):
    permission_granted: bool = False


class EmergencyStopBody(BaseModel):
    active: bool


class JobToggleBody(BaseModel):
    enabled: bool


class ApprovalDecisionBody(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.get("/health", response_model=HealthView, tags=["system"])
async def health(runtime: RuntimeDep) -> HealthView:
    return await runtime.health()


@router.get("/auth/me", tags=["auth"])
async def me(principal: PrincipalDep) -> dict[str, Any]:
    return principal.model_dump()


@router.post("/auth/pair", response_model=PairResponse, tags=["auth"])
async def pair_device(body: PairRequest, runtime: RuntimeDep) -> PairResponse:
    return runtime.auth.pair(code=body.code, device_name=body.device_name, platform=body.platform)


@router.post("/auth/pairing-code", response_model=PairingCodeView, tags=["auth"])
async def issue_pairing_code(principal: PrincipalDep, runtime: RuntimeDep) -> PairingCodeView:
    return runtime.auth.issue_pairing_code(principal)


@router.get("/devices", response_model=list[DeviceView], tags=["auth"])
async def list_devices(principal: PrincipalDep, runtime: RuntimeDep) -> list[DeviceView]:
    return runtime.auth.list_devices()


@router.post("/devices/{device_id}/revoke", tags=["auth"])
async def revoke_device(device_id: str, principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    runtime.auth.revoke_device(device_id, actor=principal.user_id)
    return {"revoked": device_id}


@router.post("/chat", status_code=202, response_model=ChatResponse, tags=["tasks"])
async def chat(body: ChatRequest, principal: PrincipalDep, runtime: RuntimeDep) -> ChatResponse:
    task_id = runtime.orchestrator.create_task(
        request=body.message, user_id=principal.user_id, device_id=principal.device_id,
        autonomy_level=body.autonomy_level,
    )
    await runtime.orchestrator.enqueue(task_id)
    return ChatResponse(task=runtime.store.task_view(task_id), reply=None)


@router.post("/tasks", status_code=202, response_model=TaskView, tags=["tasks"])
async def create_task(body: ChatRequest, principal: PrincipalDep, runtime: RuntimeDep) -> TaskView:
    task_id = runtime.orchestrator.create_task(
        request=body.message, user_id=principal.user_id, device_id=principal.device_id,
        autonomy_level=body.autonomy_level,
    )
    await runtime.orchestrator.enqueue(task_id)
    return runtime.store.task_view(task_id)


@router.get("/tasks", tags=["tasks"])
async def list_tasks(
    principal: PrincipalDep, runtime: RuntimeDep,
    limit: int = Query(default=50, ge=1, le=200), state: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    return runtime.store.list_tasks(limit=limit, state=state)


@router.get("/tasks/{task_id}", response_model=TaskView, tags=["tasks"])
async def get_task(task_id: str, principal: PrincipalDep, runtime: RuntimeDep) -> TaskView:
    return runtime.store.task_view(task_id)


@router.post("/tasks/{task_id}/cancel", response_model=TaskView, tags=["tasks"])
async def cancel_task(task_id: str, principal: PrincipalDep, runtime: RuntimeDep) -> TaskView:
    return await runtime.orchestrator.cancel(task_id)


@router.get("/tasks/{task_id}/audit", response_model=list[AuditEventView], tags=["tasks"])
async def task_audit(task_id: str, principal: PrincipalDep, runtime: RuntimeDep) -> list[AuditEventView]:
    runtime.store.get_task_row(task_id)
    return runtime.store.list_audit(limit=200, task_id=task_id)


@router.get("/tasks/{task_id}/agents", tags=["tasks"])
async def task_agents(task_id: str, principal: PrincipalDep, runtime: MatrixRuntime) -> dict[str, Any]:
    runtime.store.get_task_row(task_id)
    events = runtime.store.list_audit(limit=500, task_id=task_id)
    selection = next((event for event in events if event.event_type == "task.specialists_selected"), None)
    worker_runs = [
        {**event.detail, "status": event.status}
        for event in events if event.event_type == "task.agent_worker_run"
    ]
    return {
        "task_id": task_id, "selected_agents": selection.detail.get("agents", []) if selection else [],
        "context_injected": bool(selection and selection.detail.get("context_injected")),
        "routing_strategy": selection.detail.get("routing_strategy") if selection else None,
        "worker_runs": worker_runs, "worker_count": len(worker_runs),
        "successful_workers": sum(run.get("status") == "completed" for run in worker_runs),
        "status": selection.status if selection else "not_routed",
    }


@router.post(
    "/tasks/{task_id}/approvals/{approval_id}/approve", response_model=TaskView, tags=["tasks"]
)
async def approve_action(
    task_id: str, approval_id: str, principal: PrincipalDep, runtime: RuntimeDep,
    body: ApprovalDecisionBody | None = None,
) -> TaskView:
    view = await runtime.orchestrator.decide_approval(
        task_id, approval_id, approved=True, decided_by=principal.user_id
    )
    runtime.store.audit(
        "approval.owner_approved", actor=principal.user_id, task_id=task_id, status="approved",
        detail={"approval_id": approval_id, "reason": (body.reason if body else None)},
    )
    return view


@router.post("/tasks/{task_id}/approvals/{approval_id}/deny", response_model=TaskView, tags=["tasks"])
async def deny_action(
    task_id: str, approval_id: str, principal: PrincipalDep, runtime: RuntimeDep,
    body: ApprovalDecisionBody | None = None,
) -> TaskView:
    view = await runtime.orchestrator.decide_approval(
        task_id, approval_id, approved=False, decided_by=principal.user_id
    )
    runtime.store.audit(
        "approval.owner_denied", actor=principal.user_id, task_id=task_id, status="denied",
        detail={"approval_id": approval_id, "reason": (body.reason if body else None)},
    )
    return view


@router.get("/tools", response_model=list[ToolSpec], tags=["tools"])
async def list_tools(principal: PrincipalDep, runtime: RuntimeDep) -> list[ToolSpec]:
    return runtime.tool_catalog()


@router.post("/tools/{tool_name}/execute", status_code=202, response_model=TaskView, tags=["tools"])
async def execute_tool(
    tool_name: str, body: ToolExecuteRequest, principal: PrincipalDep, runtime: RuntimeDep
) -> TaskView:
    task_id = runtime.orchestrator.create_direct_tool_task(
        tool_name, body.arguments, user_id=principal.user_id,
        autonomy_level=body.autonomy_level, scopes=body.scopes,
    )
    await runtime.orchestrator.enqueue(task_id)
    return runtime.store.task_view(task_id)


@router.get("/memory", response_model=list[MemoryView], tags=["memory"])
async def list_memory(
    principal: PrincipalDep, runtime: RuntimeDep,
    limit: int = Query(default=50, ge=1, le=200), kind: str | None = Query(default=None),
) -> list[MemoryView]:
    return runtime.memory.list(limit=limit, kind=kind)


@router.get("/memory/search", tags=["memory"])
async def search_memory(
    principal: PrincipalDep, runtime: RuntimeDep,
    q: str = Query(min_length=1, max_length=500), limit: int = Query(default=10, ge=1, le=50),
    kinds: str | None = Query(default=None),
) -> dict[str, Any]:
    kind_list = [kind.strip() for kind in kinds.split(",") if kind.strip()] if kinds else []
    items = runtime.memory.search(q, kinds=kind_list, limit=limit)
    return {"query": q, "count": len(items), "items": items}


@router.get("/memory/export", tags=["memory"])
async def export_memory(principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    items = runtime.memory.export()
    return {"count": len(items), "items": items}


@router.post("/memory", status_code=201, response_model=MemoryView, tags=["memory"])
async def create_memory(body: MemoryCreate, principal: PrincipalDep, runtime: RuntimeDep) -> MemoryView:
    return runtime.memory.add(
        kind=body.kind, title=body.title, content=body.content, source=body.source,
        confidence=body.confidence, expires_at=body.expires_at,
    )


@router.patch("/memory/{item_id}", response_model=MemoryView, tags=["memory"])
async def update_memory(
    item_id: str, body: MemoryUpdate, principal: PrincipalDep, runtime: RuntimeDep
) -> MemoryView:
    return runtime.memory.update(item_id, title=body.title, content=body.content)


@router.delete("/memory/{item_id}", tags=["memory"])
async def delete_memory(item_id: str, principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    runtime.memory.delete(item_id)
    runtime.store.audit("memory.deleted", actor=principal.user_id, status="ok", detail={"item_id": item_id})
    return {"deleted": item_id}


def _voice_session_dict(session) -> dict[str, Any]:
    return {
        "session_id": session.id, "state": session.state, "muted": session.muted,
        "started_at": session.started_at, "last_utterance": session.last_utterance,
    }


@router.get("/voice/status", response_model=VoiceStatusView, tags=["voice"])
async def voice_status(principal: PrincipalDep, runtime: RuntimeDep) -> VoiceStatusView:
    return runtime.voice.status()


@router.post("/voice/start", tags=["voice"])
async def voice_start(body: VoiceStartBody, principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    session = runtime.voice.start(muted=body.muted)
    runtime.store.audit(
        "voice.session_started", actor=principal.user_id, status="listening",
        detail={"muted": session.muted},
    )
    return _voice_session_dict(session)


@router.post("/voice/mute", tags=["voice"])
async def voice_mute(principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    session = runtime.voice.mute()
    runtime.store.audit("voice.muted", actor=principal.user_id, status="muted")
    return _voice_session_dict(session)


@router.post("/voice/unmute", tags=["voice"])
async def voice_unmute(principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    return _voice_session_dict(runtime.voice.unmute())


@router.post("/voice/stop", tags=["voice"])
async def voice_stop(principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    session = runtime.voice.stop()
    runtime.store.audit("voice.session_stopped", actor=principal.user_id, status="idle")
    return _voice_session_dict(session)


@router.post("/voice/transcribe", tags=["voice"])
async def voice_transcribe(
    body: VoiceTranscribeBody, principal: PrincipalDep, runtime: RuntimeDep
) -> dict[str, Any]:
    return await asyncio.to_thread(
        runtime.voice.transcribe, body.audio_base64, session_id=body.session_id
    )


@router.post("/voice/speak", tags=["voice"])
async def voice_speak(
    body: VoiceSpeakBody, principal: PrincipalDep, runtime: RuntimeDep
) -> dict[str, Any]:
    return await runtime.voice.speak(body.text, session_id=body.session_id)


@router.post("/voice/barge-in", tags=["voice"])
async def voice_barge_in(principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    result = runtime.voice.barge_in()
    runtime.store.audit(
        "voice.barge_in", actor=principal.user_id, status="listening",
        detail={"interrupted": result["interrupted"]},
    )
    return result


# Vision routes continue below in the existing router module.
