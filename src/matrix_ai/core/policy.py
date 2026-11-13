"""Server-side policy engine and permission broker.

Policy is evaluated *after* the model produced a plan and *again* immediately
before a tool runs, using the live emergency-stop flag. A model claim such as
"risk: low" is ignored: risk comes from the tool declaration and, for untrusted
content, is escalated by the policy engine itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..config import MatrixConfig
from ..core.models import DEFAULT_SCOPES, AutonomyLevel, Plan, Risk, ToolSpec, action_fingerprint


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    risk: Risk
    permission_scope: str
    reason: str
    requires_approval: bool = False
    code: str = "ok"
    autonomy_level: int = 2
    tainted: bool = False
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "risk": self.risk.value,
            "permission_scope": self.permission_scope,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "code": self.code,
            "tainted": self.tainted,
        }


#: Minimum autonomy level required to even consider a risk class.
REQUIRED_AUTONOMY: dict[Risk, int] = {
    Risk.LOW: int(AutonomyLevel.OBSERVE),
    Risk.MEDIUM: int(AutonomyLevel.SAFE_EXECUTION),
    Risk.HIGH: int(AutonomyLevel.SAFE_EXECUTION),
    Risk.CRITICAL: int(AutonomyLevel.SAFE_EXECUTION),
}


class PolicyEngine:
    def __init__(self, config: MatrixConfig, store=None) -> None:
        self.config = config
        self.store = store

    # -- helpers ----------------------------------------------------------
    @property
    def emergency_stop(self) -> bool:
        if self.store is None:
            return False
        return bool(self.store.emergency_stop)

    def task_scopes(self, requested: Sequence[str] | None) -> list[str]:
        """Narrow a task's scopes; never silently widen them."""
        allowed = set(DEFAULT_SCOPES) | set(self.config.additional_scopes)
        if not requested:
            return sorted(allowed)
        return sorted({scope for scope in requested if scope in allowed})

    def effective_risk(self, base: Risk, *, tainted: bool) -> Risk:
        """Untrusted content can never lower risk; one step of escalation."""
        return base.bump() if tainted else base

    def approval_fingerprint(self, tool_name: str, arguments: dict) -> str:
        return action_fingerprint(tool_name, arguments)

    # -- evaluation -------------------------------------------------------
    def evaluate_call(
        self,
        spec: ToolSpec,
        *,
        scopes: Sequence[str],
        autonomy: int,
        risk: Risk | None = None,
        tainted: bool = False,
    ) -> PolicyDecision:
        effective = self.effective_risk(risk or spec.risk, tainted=tainted)

        if self.emergency_stop:
            return PolicyDecision(
                allowed=False,
                risk=effective,
                permission_scope=spec.permission_scope,
                reason="Emergency stop is active; no tool execution is permitted.",
                code="emergency_stop",
                autonomy_level=int(autonomy),
                tainted=tainted,
            )

        granted = set(scopes)
        if "*" not in granted and spec.permission_scope not in granted:
            return PolicyDecision(
                allowed=False,
                risk=effective,
                permission_scope=spec.permission_scope,
                reason=f"Scope '{spec.permission_scope}' is not granted to this task.",
                code="scope_not_granted",
                autonomy_level=int(autonomy),
                tainted=tainted,
            )

        required = REQUIRED_AUTONOMY[effective]
        if int(autonomy) < required:
            return PolicyDecision(
                allowed=False,
                risk=effective,
                permission_scope=spec.permission_scope,
                reason=(
                    f"Autonomy level L{int(autonomy)} is below L{required} required for "
                    f"{effective.value}-risk actions."
                ),
                code="autonomy_too_low",
                autonomy_level=int(autonomy),
                tainted=tainted,
            )

        if effective is Risk.CRITICAL:
            return PolicyDecision(
                allowed=True,
                risk=effective,
                permission_scope=spec.permission_scope,
                reason="Critical action requires exact per-action confirmation.",
                requires_approval=True,
                code="approval_required",
                autonomy_level=int(autonomy),
                tainted=tainted,
                details={"approval_ttl_seconds": self.config.approval_ttl_seconds},
            )

        if effective is Risk.HIGH:
            return PolicyDecision(
                allowed=True,
                risk=effective,
                permission_scope=spec.permission_scope,
                reason=(
                    "Action influenced by untrusted content requires approval and a preview."
                    if tainted
                    else "High-risk action requires explicit approval and a preview."
                ),
                requires_approval=True,
                code="approval_required",
                autonomy_level=int(autonomy),
                tainted=tainted,
                details={"approval_ttl_seconds": self.config.approval_ttl_seconds},
            )

        return PolicyDecision(
            allowed=True,
            risk=effective,
            permission_scope=spec.permission_scope,
            reason="Permitted within granted scope.",
            code="ok",
            autonomy_level=int(autonomy),
            tainted=tainted,
        )

    def evaluate_plan(
        self, plan: Plan, *, scopes: Sequence[str], autonomy: int, specs: dict[str, ToolSpec]
    ) -> PolicyDecision:
        """Plan-level gate. Blocks before any side effect; no approval is granted here."""
        if self.emergency_stop:
            return PolicyDecision(
                allowed=False,
                risk=Risk.LOW,
                permission_scope="plan",
                reason="Emergency stop is active; new work is blocked.",
                code="emergency_stop",
                autonomy_level=int(autonomy),
            )
        if len(plan.steps) > 12:
            return PolicyDecision(
                allowed=False,
                risk=Risk.MEDIUM,
                permission_scope="plan",
                reason="Plan exceeds the 12-step safety limit.",
                code="plan_too_large",
                autonomy_level=int(autonomy),
            )
        granted = set(scopes)
        highest = Risk.LOW
        for step in plan.steps:
            if step.kind.value != "tool":
                continue
            spec = specs.get(step.tool_name or "")
            if spec is None:
                return PolicyDecision(
                    allowed=False,
                    risk=Risk.MEDIUM,
                    permission_scope="plan",
                    reason=f"Plan references unknown tool '{step.tool_name}'.",
                    code="unknown_tool",
                    autonomy_level=int(autonomy),
                )
            if "*" not in granted and spec.permission_scope not in granted:
                return PolicyDecision(
                    allowed=False,
                    risk=spec.risk,
                    permission_scope=spec.permission_scope,
                    reason=f"Plan requires scope '{spec.permission_scope}' that is not granted.",
                    code="scope_not_granted",
                    autonomy_level=int(autonomy),
                )
            if spec.risk.level > highest.level:
                highest = spec.risk
        requires_approval = highest.at_least(Risk.HIGH)
        return PolicyDecision(
            allowed=True,
            risk=highest,
            permission_scope="plan",
            reason="Plan accepted for step-by-step policy evaluation.",
            requires_approval=requires_approval,
            code="ok",
            autonomy_level=int(autonomy),
        )
