"""HTTP surface.

Routes authenticate, validate input and delegate. They never run shell commands,
never evaluate policy themselves and never fabricate provider confirmations.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..access import RemoteAccessManager
from ..core.agents import agent_catalog, get_agency_agent, route_agency_agents, search_agency_agents
from ..core.goal_graph import GoalGraph, GoalNode
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


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------
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


class GoalCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=2000)
    success_criteria: str = Field(default="Complete the goal and verify the outcome.", min_length=1, max_length=2000)
    autonomy_level: int | None = Field(default=None, ge=0, le=4)
    deadline_seconds: float = Field(default=900.0, ge=1.0, le=86400.0)


class GoalPlanNodeBody(BaseModel):
    node_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    depends_on: list[str] = Field(default_factory=list, max_length=16)


class GoalPlanCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=2000)
    success_criteria: str = Field(default="Complete the goal and verify the outcome.", min_length=1, max_length=2000)
    deadline_seconds: float = Field(default=900.0, ge=1.0, le=86400.0)
    nodes: list[GoalPlanNodeBody] = Field(min_length=1, max_length=64)


class ApprovalDecisionBody(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# health & identity
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# chat & tasks
# ---------------------------------------------------------------------------
@router.post("/chat", status_code=202, response_model=ChatResponse, tags=["tasks"])
async def chat(body: ChatRequest, principal: PrincipalDep, runtime: RuntimeDep) -> ChatResponse:
    task_id = runtime.orchestrator.create_task(
        request=body.message,
        user_id=principal.user_id,
        device_id=principal.device_id,
        autonomy_level=body.autonomy_level,
    )
    await runtime.orchestrator.enqueue(task_id)
    return ChatResponse(task=runtime.store.task_view(task_id), reply=None)


@router.post("/tasks", status_code=202, response_model=TaskView, tags=["tasks"])
async def create_task(body: ChatRequest, principal: PrincipalDep, runtime: RuntimeDep) -> TaskView:
    task_id = runtime.orchestrator.create_task(
        request=body.message,
        user_id=principal.user_id,
        device_id=principal.device_id,
        autonomy_level=body.autonomy_level,
    )
    await runtime.orchestrator.enqueue(task_id)
    return runtime.store.task_view(task_id)


@router.get("/tasks", tags=["tasks"])
async def list_tasks(
    principal: PrincipalDep,
    runtime: RuntimeDep,
    limit: int = Query(default=50, ge=1, le=200),
    state: str | None = Query(default=None),
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
async def task_agents(
    task_id: str, principal: PrincipalDep, runtime: RuntimeDep
) -> dict[str, Any]:
    runtime.store.get_task_row(task_id)
    events = runtime.store.list_audit(limit=500, task_id=task_id)
    selection = next(
        (event for event in events if event.event_type == "task.specialists_selected"),
        None,
    )
    worker_runs = [
        {**event.detail, "status": event.status}
        for event in events if event.event_type == "task.agent_worker_run"
    ]
    return {
        "task_id": task_id,
        "selected_agents": selection.detail.get("agents", []) if selection else [],
        "context_injected": bool(selection and selection.detail.get("context_injected")),
        "routing_strategy": selection.detail.get("routing_strategy") if selection else None,
        "worker_runs": worker_runs,
        "worker_count": len(worker_runs),
        "successful_workers": sum(run.get("status") == "completed" for run in worker_runs),
        "status": selection.status if selection else "not_routed",
    }


@router.post(
    "/tasks/{task_id}/approvals/{approval_id}/approve", response_model=TaskView, tags=["tasks"]
)
async def approve_action(
    task_id: str,
    approval_id: str,
    principal: PrincipalDep,
    runtime: RuntimeDep,
    body: ApprovalDecisionBody | None = None,
) -> TaskView:
    view = await runtime.orchestrator.decide_approval(
        task_id, approval_id, approved=True, decided_by=principal.user_id
    )
    runtime.store.audit(
        "approval.owner_approved",
        actor=principal.user_id,
        task_id=task_id,
        status="approved",
        detail={"approval_id": approval_id, "reason": (body.reason if body else None)},
    )
    return view


@router.post("/tasks/{task_id}/approvals/{approval_id}/deny", response_model=TaskView, tags=["tasks"])
async def deny_action(
    task_id: str,
    approval_id: str,
    principal: PrincipalDep,
    runtime: RuntimeDep,
    body: ApprovalDecisionBody | None = None,
) -> TaskView:
    view = await runtime.orchestrator.decide_approval(
        task_id, approval_id, approved=False, decided_by=principal.user_id
    )
    runtime.store.audit(
        "approval.owner_denied",
        actor=principal.user_id,
        task_id=task_id,
        status="denied",
        detail={"approval_id": approval_id, "reason": (body.reason if body else None)},
    )
    return view


# ---------------------------------------------------------------------------
# durable goals
# ---------------------------------------------------------------------------
@router.post("/goals", status_code=202, tags=["goals"])
async def create_goal(
    body: GoalCreateBody, principal: PrincipalDep, runtime: RuntimeDep
) -> dict[str, Any]:
    goal_id = runtime.store.create_goal(
        owner_id=principal.user_id,
        title=body.title,
        success_criteria=body.success_criteria,
        deadline_seconds=min(body.deadline_seconds, runtime.config.task_deadline_seconds),
    )
    try:
        task_id = runtime.orchestrator.create_task(
            request=f"Goal: {body.title}\nSuccess criteria: {body.success_criteria}",
            user_id=principal.user_id,
            autonomy_level=body.autonomy_level,
            kind="goal",
            goal_id=goal_id,
            deadline_seconds=body.deadline_seconds,
        )
        runtime.store.attach_goal_task(goal_id, task_id)
        await runtime.orchestrator.enqueue(task_id)
    except Exception:
        runtime.store.fail_goal_start(goal_id, "MATRIX could not start this goal; inspect system health and retry.")
        runtime.store.audit(
            "goal.start_failed", actor=principal.user_id, status="failed",
            detail={"goal_id": goal_id},
        )
        raise
    runtime.store.audit(
        "goal.created", actor=principal.user_id, task_id=task_id, status="running",
        detail={"goal_id": goal_id},
    )
    return runtime.store.get_goal(goal_id)


@router.post("/goals/plan", status_code=201, tags=["goals"])
async def create_goal_plan(
    body: GoalPlanCreateBody, principal: PrincipalDep, runtime: RuntimeDep
) -> dict[str, Any]:
    nodes = [GoalNode(node.node_id, tuple(node.depends_on)) for node in body.nodes]
    try:
        graph = GoalGraph(nodes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    goal_id = runtime.store.create_goal(
        owner_id=principal.user_id,
        title=body.title,
        success_criteria=body.success_criteria,
        deadline_seconds=min(body.deadline_seconds, runtime.config.task_deadline_seconds),
    )
    runtime.store.save_goal_graph(
        goal_id,
        [
            {
                "node_id": node.node_id,
                "title": next(item.title for item in body.nodes if item.node_id == node.node_id),
                "depends_on": list(node.depends_on),
            }
            for node in nodes
        ],
    )
    runtime.store.audit(
        "goal.graph_planned", actor=principal.user_id, status="planned",
        detail={"goal_id": goal_id, "node_count": len(nodes), "topological_order": graph.topological_order},
    )
    result = runtime.store.get_goal(goal_id)
    result["nodes"] = runtime.store.get_goal_graph(goal_id)
    result["execution_status"] = "plan_only"
    return result


@router.get("/goals/{goal_id}/graph", tags=["goals"])
async def get_goal_graph(
    goal_id: str, principal: PrincipalDep, runtime: RuntimeDep
) -> dict[str, Any]:
    goal = runtime.store.get_goal(goal_id)
    if goal["owner_id"] != principal.user_id:
        raise NotFoundError(f"goal {goal_id} not found")
    return {
        "goal_id": goal_id,
        "nodes": runtime.store.get_goal_graph(goal_id),
        "execution_status": "plan_only" if goal["state"] == "planned" else goal["state"],
    }


@router.post("/goals/{goal_id}/start", status_code=202, tags=["goals"])
async def start_goal_plan(
    goal_id: str, principal: PrincipalDep, runtime: RuntimeDep
) -> dict[str, Any]:
    goal = runtime.store.get_goal(goal_id)
    if goal["owner_id"] != principal.user_id:
        raise NotFoundError(f"goal {goal_id} not found")
    if goal["state"] not in {"planned", "running"}:
        raise HTTPException(status_code=409, detail="goal plan is not startable")
    dispatched = await runtime.orchestrator.dispatch_goal_ready_nodes(goal_id)
    refreshed = runtime.store.get_goal(goal_id)
    nodes = runtime.store.get_goal_graph(goal_id)
    return {
        "goal_id": goal_id,
        "state": refreshed["state"],
        "nodes": nodes,
        "dispatched_task_ids": dispatched,
        "execution_status": "running" if dispatched or any(n["state"] == "running" for n in nodes) else refreshed["state"],
    }


@router.get("/goals", tags=["goals"])
async def list_goals(
    principal: PrincipalDep, runtime: RuntimeDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    return runtime.store.list_goals(owner_id=principal.user_id, limit=limit)


@router.get("/goals/{goal_id}", tags=["goals"])
async def get_goal(
    goal_id: str, principal: PrincipalDep, runtime: RuntimeDep
) -> dict[str, Any]:
    goal = runtime.store.get_goal(goal_id)
    if goal["owner_id"] != principal.user_id:
        raise NotFoundError(f"goal {goal_id} not found")
    return goal


@router.post("/goals/{goal_id}/cancel", tags=["goals"])
async def cancel_goal(
    goal_id: str, principal: PrincipalDep, runtime: RuntimeDep
) -> dict[str, Any]:
    goal = runtime.store.get_goal(goal_id)
    if goal["owner_id"] != principal.user_id:
        raise NotFoundError(f"goal {goal_id} not found")
    if goal["task_id"] and goal["state"] in {"queued", "running"}:
        await runtime.orchestrator.cancel(goal["task_id"])
    return runtime.store.get_goal(goal_id)


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------
@router.get("/tools", response_model=list[ToolSpec], tags=["tools"])
async def list_tools(principal: PrincipalDep, runtime: RuntimeDep) -> list[ToolSpec]:
    return runtime.tool_catalog()


@router.post("/tools/{tool_name}/execute", status_code=202, response_model=TaskView, tags=["tools"])
async def execute_tool(
    tool_name: str, body: ToolExecuteRequest, principal: PrincipalDep, runtime: RuntimeDep
) -> TaskView:
    task_id = runtime.orchestrator.create_direct_tool_task(
        tool_name,
        body.arguments,
        user_id=principal.user_id,
        autonomy_level=body.autonomy_level,
        scopes=body.scopes,
    )
    await runtime.orchestrator.enqueue(task_id)
    return runtime.store.task_view(task_id)


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------
@router.get("/memory", response_model=list[MemoryView], tags=["memory"])
async def list_memory(
    principal: PrincipalDep,
    runtime: RuntimeDep,
    limit: int = Query(default=50, ge=1, le=200),
    kind: str | None = Query(default=None),
) -> list[MemoryView]:
    return runtime.memory.list(limit=limit, kind=kind)


@router.get("/memory/search", tags=["memory"])
async def search_memory(
    principal: PrincipalDep,
    runtime: RuntimeDep,
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=10, ge=1, le=50),
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
        kind=body.kind,
        title=body.title,
        content=body.content,
        source=body.source,
        confidence=body.confidence,
        expires_at=body.expires_at,
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


# ---------------------------------------------------------------------------
# voice
# ---------------------------------------------------------------------------
def _voice_session_dict(session) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "state": session.state,
        "muted": session.muted,
        "started_at": session.started_at,
        "last_utterance": session.last_utterance,
    }


@router.get("/voice/status", response_model=VoiceStatusView, tags=["voice"])
async def voice_status(principal: PrincipalDep, runtime: RuntimeDep) -> VoiceStatusView:
    return runtime.voice.status()


@router.post("/voice/start", tags=["voice"])
async def voice_start(
    body: VoiceStartBody, principal: PrincipalDep, runtime: RuntimeDep
) -> dict[str, Any]:
    session = runtime.voice.start(muted=body.muted)
    runtime.store.audit(
        "voice.session_started", actor=principal.user_id, status="listening", detail={"muted": session.muted}
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
        "voice.barge_in", actor=principal.user_id, status="listening", detail={"interrupted": result["interrupted"]}
    )
    return result


# ---------------------------------------------------------------------------
# vision
# ---------------------------------------------------------------------------
def _vision_session_dict(session) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "state": session.state,
        "frames": session.frames,
        "started_at": session.started_at,
        "stopped_at": session.stopped_at,
    }


@router.get("/vision/status", response_model=VisionStatusView, tags=["vision"])
async def vision_status(principal: PrincipalDep, runtime: RuntimeDep) -> VisionStatusView:
    return runtime.vision.status()


@router.post("/vision/sessions", status_code=201, tags=["vision"])
async def vision_start(
    body: VisionStartBody, principal: PrincipalDep, runtime: RuntimeDep
) -> dict[str, Any]:
    session = runtime.vision.create_session(permission_granted=body.permission_granted)
    runtime.store.audit("vision.session_started", actor=principal.user_id, status="active")
    return _vision_session_dict(session)


@router.post("/vision/sessions/{session_id}/capture", tags=["vision"])
async def vision_capture(session_id: str, principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    return runtime.vision.capture(session_id)


@router.post("/vision/sessions/{session_id}/stop", tags=["vision"])
async def vision_stop(session_id: str, principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    session = runtime.vision.stop(session_id)
    runtime.store.audit("vision.session_stopped", actor=principal.user_id, status="stopped")
    return _vision_session_dict(session)


# ---------------------------------------------------------------------------
# integrations
# ---------------------------------------------------------------------------
@router.get("/integrations", response_model=list[IntegrationView], tags=["integrations"])
async def list_integrations(principal: PrincipalDep, runtime: RuntimeDep) -> list[IntegrationView]:
    return runtime.integrations.views()


@router.post("/integrations/{name}/check", response_model=IntegrationView, tags=["integrations"])
async def check_integration(name: str, principal: PrincipalDep, runtime: RuntimeDep) -> IntegrationView:
    view = await runtime.integrations.check(name)
    runtime.store.audit(
        "integration.checked",
        actor=principal.user_id,
        status=view.status,
        detail={"integration": name},
    )
    return view


# ---------------------------------------------------------------------------
# security, system, settings
# ---------------------------------------------------------------------------
@router.get("/security/audit", response_model=list[AuditEventView], tags=["security"])
async def audit_log(
    principal: PrincipalDep,
    runtime: RuntimeDep,
    limit: int = Query(default=100, ge=1, le=500),
    task_id: str | None = Query(default=None),
) -> list[AuditEventView]:
    return runtime.store.list_audit(limit=limit, task_id=task_id)


@router.get("/security/policy", tags=["security"])
async def policy_state(principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    tools = [
        {
            "name": spec.name,
            "risk": spec.risk.value,
            "permission_scope": spec.permission_scope,
            "requires_approval": spec.risk in {Risk.HIGH, Risk.CRITICAL},
            "idempotent": spec.idempotent,
            "timeout_seconds": spec.timeout_seconds,
        }
        for spec in runtime.tool_catalog()
    ]
    return {
        "emergency_stop": runtime.store.emergency_stop,
        "autonomy_level": runtime.config.autonomy_level,
        "approval_ttl_seconds": runtime.config.approval_ttl_seconds,
        "default_scopes": list(DEFAULT_SCOPES),
        "tools": tools,
    }


@router.post("/security/emergency-stop", tags=["security"])
async def emergency_stop(
    body: EmergencyStopBody, principal: PrincipalDep, runtime: RuntimeDep
) -> dict[str, Any]:
    runtime.store.set_emergency_stop(body.active, actor=principal.user_id)
    cancelled = await runtime.orchestrator.cancel_all() if body.active else 0
    return {"active": body.active, "cancelled_tasks": cancelled}


@router.get("/system/status", tags=["system"])
async def system_status(principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    return await runtime.readonly_tool_call("system.get_status", {}, principal=principal)


@router.get("/models", tags=["system"])
async def models(principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    return {
        "status": await runtime.models.status(),
        "hardware": runtime.models.hardware(),
        "prompt_versions": {"plan": PROMPT_VERSION, "chat": "chat.v1"},
    }


@router.get("/status", tags=["system"])
async def dashboard_status(principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    return {
        "version": runtime.version,
        "health": await runtime.health(),
        "running_tasks": runtime.orchestrator.running_count,
        "recent_tasks": runtime.store.list_tasks(limit=8),
        "counts": runtime.store.db.table_counts(),
        "emergency_stop": runtime.store.emergency_stop,
        "integrations": {
            "configured": runtime.integrations.configured_count,
            "total": runtime.integrations.total_count,
        },
    }


@router.get("/workers", tags=["agents"])
async def list_workers(principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    return {**runtime.worker_pool.status(), "workers": runtime.worker_pool.list_workers()}


@router.get("/workers/{slug}", tags=["agents"])
async def worker_detail(slug: str, principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    worker = runtime.worker_pool.get_worker(slug)
    if worker is None:
        raise NotFoundError(f"worker {slug} not found")
    return worker


@router.get("/agents", tags=["system"])
async def agents(principal: PrincipalDep) -> list[dict[str, Any]]:
    return agent_catalog()


@router.get("/agents/search", tags=["system"])
async def search_agents(
    principal: PrincipalDep,
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=5, ge=1, le=10),
) -> dict[str, Any]:
    matches = search_agency_agents(q, limit=limit)
    return {"query": q, "count": len(matches), "agents": matches}


@router.get("/agents/route", tags=["system"])
async def route_agents(
    principal: PrincipalDep,
    q: str = Query(min_length=1, max_length=8000),
) -> dict[str, Any]:
    selected = route_agency_agents(q)
    return {
        "query": q,
        "strategy": "automatic_hybrid_full_profile_search_v1",
        "agent_count": len(selected),
        "agents": [
            {key: value for key, value in agent.items() if key != "content"}
            for agent in selected
        ],
    }


@router.get("/agents/{slug}", tags=["system"])
async def agency_agent_detail(slug: str, principal: PrincipalDep) -> dict[str, Any]:
    agent = get_agency_agent(slug)
    if agent is None:
        raise NotFoundError(f"specialist agent '{slug}' not found")
    return agent


@router.get("/settings", tags=["system"])
async def settings(principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    return runtime.config.public_dict()


# ---------------------------------------------------------------------------
# scheduler
# ---------------------------------------------------------------------------
@router.get("/automation/status", tags=["automation"])
async def automation_status(principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    jobs = runtime.store.list_jobs()
    return {
        "scheduler_running": runtime.scheduler.running,
        "emergency_stop": runtime.store.emergency_stop,
        "configured_autonomy_level": runtime.config.autonomy_level,
        "maximum_scheduled_job_autonomy": min(runtime.config.autonomy_level, 2),
        "jobs_total": len(jobs),
        "jobs_enabled": sum(1 for job in jobs if job.enabled),
        "policy": {
            "high_risk_requires_exact_approval": True,
            "critical_risk_requires_exact_approval": True,
            "agents_can_grant_themselves_scopes": False,
            "agents_can_approve_their_own_actions": False,
        },
    }


@router.get("/access/remote", tags=["access"])
async def remote_access_status(principal: PrincipalDep) -> dict[str, Any]:
    return await asyncio.to_thread(RemoteAccessManager().status)


@router.get("/jobs", response_model=list[JobView], tags=["automation"])
async def list_jobs(principal: PrincipalDep, runtime: RuntimeDep) -> list[JobView]:
    return runtime.store.list_jobs()


@router.post("/jobs", status_code=201, response_model=JobView, tags=["automation"])
async def create_job(body: JobCreate, principal: PrincipalDep, runtime: RuntimeDep) -> JobView:
    job_id = runtime.store.create_job(
        name=body.name,
        prompt=body.prompt,
        interval_seconds=body.interval_seconds,
        autonomy_level=body.autonomy_level,
        enabled=body.enabled,
    )
    runtime.store.audit(
        "scheduler.job_created",
        actor=principal.user_id,
        status="ok",
        detail={"job_id": job_id, "interval_seconds": body.interval_seconds},
    )
    jobs = [job for job in runtime.store.list_jobs() if job.id == job_id]
    if not jobs:
        raise NotFoundError("job was not persisted")
    return jobs[0]


@router.post("/jobs/{job_id}/run", status_code=202, tags=["automation"])
async def run_job(job_id: str, principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    jobs = [job for job in runtime.store.list_jobs() if job.id == job_id]
    if not jobs:
        raise NotFoundError(f"job {job_id} not found")
    job = jobs[0]
    task_id = runtime.orchestrator.create_task(
        request=job.prompt,
        user_id=principal.user_id,
        autonomy_level=min(int(job.autonomy_level), 1),
        kind="job",
    )
    await runtime.orchestrator.enqueue(task_id)
    runtime.store.mark_job_run(job.id, status="queued", interval_seconds=job.interval_seconds)
    return {"job_id": job.id, "task": runtime.store.task_view(task_id)}


@router.patch("/jobs/{job_id}", response_model=JobView, tags=["automation"])
async def toggle_job(
    job_id: str, body: JobToggleBody, principal: PrincipalDep, runtime: RuntimeDep
) -> JobView:
    runtime.store.set_job_enabled(job_id, body.enabled)
    jobs = [job for job in runtime.store.list_jobs() if job.id == job_id]
    if not jobs:
        raise NotFoundError(f"job {job_id} not found")
    return jobs[0]


@router.get("/debug/tools/{tool_name}/schema", tags=["tools"])
async def tool_schema(tool_name: str, principal: PrincipalDep, runtime: RuntimeDep) -> dict[str, Any]:
    spec = runtime.registry.get(tool_name).spec()
    return {"spec": spec.model_dump(mode="json"), "schema": json.loads(spec.model_dump_json())["input_schema"]}
