"""Domain models shared by every MATRIX subsystem.

Pydantic models are the contract between the API, the orchestrator, the tool
adapters and the database. Model output is *never* trusted: it is parsed into
these typed structures before anything executes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum, IntEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).astimezone(UTC).isoformat(timespec="milliseconds")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def action_fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    """Hash binding an approval to exactly one action and its arguments."""
    return hashlib.sha256(f"{tool_name}:{canonical_json(arguments)}".encode()).hexdigest()


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def level(self) -> int:
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}[self.value]

    def bump(self) -> Risk:
        return Risk(["low", "medium", "high", "critical"][min(3, self.level + 1)])

    def at_least(self, other: Risk) -> bool:
        return self.level >= other.level


class AutonomyLevel(IntEnum):
    """L0 observe, L1 prepare, L2 safe execution, L3 conditional, L4 approval."""

    OBSERVE = 0
    PREPARE = 1
    SAFE_EXECUTION = 2
    CONDITIONAL = 3
    APPROVAL = 4


class TaskState(str, Enum):
    RECEIVED = "RECEIVED"
    UNDERSTAND = "UNDERSTAND"
    POLICY_CHECK = "POLICY_CHECK"
    PLAN = "PLAN"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    TOOL_SELECTION = "TOOL_SELECTION"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    RETRY_SAFE = "RETRY_SAFE"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_TASK_STATES = frozenset(
    {TaskState.COMPLETE, TaskState.PARTIAL, TaskState.FAILED, TaskState.CANCELLED}
)


class StepKind(str, Enum):
    TOOL = "tool"
    ANSWER = "answer"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class CallStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idx: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=300)
    kind: StepKind = StepKind.TOOL
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list)

    @field_validator("tool_name")
    @classmethod
    def _tool_required_for_tool_steps(cls, value: str | None, info) -> str | None:
        return value


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=2000)
    steps: list[PlanStep] = Field(default_factory=list, max_length=12)
    source: Literal["model", "heuristic"] = "heuristic"
    degraded: bool = False
    notes: str | None = None

    @field_validator("steps")
    @classmethod
    def _ordered_indexes(cls, steps: list[PlanStep]) -> list[PlanStep]:
        for expected, step in enumerate(steps):
            if step.idx != expected:
                raise ValueError("plan steps must be indexed 0..n-1 in order")
        return steps


class VerificationEvidence(BaseModel):
    ok: bool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    checks: list[str] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    name: str
    description: str
    permission_scope: str
    risk: Risk
    autonomy_level: int
    timeout_seconds: float
    idempotent: bool
    requires_verification: bool
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None


class ToolOutcome(BaseModel):
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    evidence: VerificationEvidence | None = None
    message: str = ""


class PlanRequest(BaseModel):
    request: str = Field(min_length=1, max_length=8000)
    context: dict[str, Any] = Field(default_factory=dict)
    scopes: list[str] | None = None


class ApprovalView(BaseModel):
    id: str
    task_id: str
    step_idx: int | None = None
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk: Risk
    status: ApprovalStatus
    created_at: str
    expires_at: str
    decided_at: str | None = None
    reason: str | None = None


class StepView(BaseModel):
    idx: int
    title: str
    kind: StepKind
    tool_name: str | None = None
    status: StepStatus
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list)
    error: str | None = None


class ToolCallView(BaseModel):
    id: str
    tool_name: str
    step_idx: int | None = None
    status: CallStatus
    attempt: int = 1
    risk: Risk = Risk.LOW
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    evidence: VerificationEvidence | None = None
    duration_ms: float = 0.0
    started_at: str | None = None
    finished_at: str | None = None


class TaskView(BaseModel):
    id: str
    state: TaskState
    original_request: str
    normalized_goal: str | None = None
    risk: Risk | None = None
    autonomy_level: int = 2
    scopes: list[str] = Field(default_factory=list)
    summary: str | None = None
    error: str | None = None
    plan_source: str | None = None
    degraded: bool = False
    retry_count: int = 0
    cancel_requested: bool = False
    model: str | None = None
    created_at: str
    updated_at: str
    evidence: dict[str, Any] | None = None
    steps: list[StepView] = Field(default_factory=list)
    tool_calls: list[ToolCallView] = Field(default_factory=list)
    approvals: list[ApprovalView] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    autonomy_level: int | None = Field(default=None, ge=0, le=4)


class ChatResponse(BaseModel):
    task: TaskView
    reply: str | None = None


class ToolExecuteRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    scopes: list[str] | None = None
    autonomy_level: int | None = Field(default=None, ge=0, le=4)


class MemoryKind(str, Enum):
    PREFERENCE = "preference"
    PROJECT = "project"
    TASK = "task"
    KNOWLEDGE = "knowledge"
    SUMMARY = "summary"


class MemoryCreate(BaseModel):
    kind: MemoryKind = MemoryKind.KNOWLEDGE
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)
    source: str = Field(default="user", max_length=500)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    expires_at: datetime | None = None


class MemoryView(BaseModel):
    id: str
    kind: MemoryKind
    title: str
    content: str
    source: str
    confidence: float
    created_at: str
    updated_at: str
    expires_at: str | None = None


class Principal(BaseModel):
    user_id: str
    device_id: str | None = None
    scope: str = "owner"
    token_id: str | None = None
    is_owner: bool = True


class PairingCodeView(BaseModel):
    code: str
    expires_at: str
    note: str = "Single-use pairing code. It is shown once and never stored in plaintext."


class PairRequest(BaseModel):
    code: str = Field(min_length=6, max_length=64)
    device_name: str = Field(min_length=1, max_length=80)
    platform: Literal["android", "windows", "web", "other"] = "android"


class PairResponse(BaseModel):
    token: str
    device_id: str
    user_id: str
    expires_at: str


class DeviceView(BaseModel):
    id: str
    name: str
    platform: str
    paired_at: str
    last_seen_at: str | None = None
    revoked: bool = False


class IntegrationView(BaseModel):
    name: str
    description: str
    category: str
    status: str
    detail: str | None = None
    required_settings: list[str] = Field(default_factory=list)
    checked_at: str | None = None


class ModelStatusView(BaseModel):
    provider: str
    model: str
    configured: bool
    reachable: bool
    status: str
    detail: str | None = None
    ram_budget_mb: int
    loaded: bool = False
    last_latency_ms: float | None = None
    last_checked: str | None = None


class VoiceStatusView(BaseModel):
    available: bool
    state: str
    muted: bool
    stt_provider: str
    tts_provider: str
    detail: str | None = None


class VisionStatusView(BaseModel):
    available: bool
    state: str
    active_sessions: int
    ocr_available: bool
    detail: str | None = None


class HealthView(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    version: str
    uptime_seconds: float
    started_at: str
    database: str
    model: ModelStatusView
    integrations_configured: int
    integrations_total: int
    emergency_stop: bool
    checks: dict[str, str] = Field(default_factory=dict)


class AuditEventView(BaseModel):
    id: int
    ts: str
    actor: str
    event_type: str
    task_id: str | None = None
    tool_name: str | None = None
    status: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class JobView(BaseModel):
    id: str
    name: str
    prompt: str
    interval_seconds: int
    autonomy_level: int
    enabled: bool
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_status: str | None = None


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=2000)
    interval_seconds: int = Field(ge=60, le=86_400)
    autonomy_level: int = Field(default=1, ge=0, le=2)
    enabled: bool = True

class JobUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    prompt: str | None = Field(default=None, min_length=1, max_length=2000)
    interval_seconds: int | None = Field(default=None, ge=60, le=86_400)
    autonomy_level: int | None = Field(default=None, ge=0, le=2)
    enabled: bool | None = None

    @field_validator("name", "prompt", mode="before")
    @classmethod
    def _strip_job_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _require_non_null_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one job field must be supplied")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("updated job fields cannot be null")
        return self


#: Default permission scopes a new task may exercise. Tools outside the granted
#: set are refused server-side even if the model asks for them.
DEFAULT_SCOPES: tuple[str, ...] = (
    "system.read",
    "files.read",
    "files.write",
    "apps.launch",
    "apps.control",
    "shell.allowlisted",
    "memory.read",
    "memory.write",
    "web.read",
    "vision.read",
)
