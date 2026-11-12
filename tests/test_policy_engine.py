"""Policy engine: risk classes, autonomy levels, scopes and taint."""

from __future__ import annotations

import pytest

from matrix_ai.config import MatrixConfig
from matrix_ai.core.models import Risk, ToolSpec
from matrix_ai.core.policy import PolicyEngine
from matrix_ai.errors import ConfigError


def make_spec(risk: Risk, scope: str = "files.write", name: str = "test.tool") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="test",
        permission_scope=scope,
        risk=risk,
        autonomy_level=2,
        timeout_seconds=10,
        idempotent=False,
        requires_verification=True,
        input_schema={},
    )


@pytest.fixture
def engine(config):
    return PolicyEngine(config)


@pytest.mark.parametrize(
    ("risk", "autonomy", "allowed", "approval"),
    [
        (Risk.LOW, 0, True, False),
        (Risk.MEDIUM, 0, False, False),
        (Risk.MEDIUM, 2, True, False),
        (Risk.HIGH, 2, True, True),
        (Risk.CRITICAL, 2, True, True),
    ],
)
def test_risk_and_autonomy_matrix(engine, risk, autonomy, allowed, approval):
    decision = engine.evaluate_call(
        make_spec(risk), scopes=["files.write"], autonomy=autonomy
    )
    assert decision.allowed is allowed
    if allowed:
        assert decision.requires_approval is approval
    else:
        assert decision.code == "autonomy_too_low"


def test_scope_must_be_granted(engine):
    decision = engine.evaluate_call(
        make_spec(Risk.LOW, scope="files.read"), scopes=["system.read"], autonomy=2
    )
    assert decision.allowed is False
    assert decision.code == "scope_not_granted"


def test_wildcard_scope_is_explicit(engine):
    decision = engine.evaluate_call(make_spec(Risk.LOW), scopes=["*"], autonomy=2)
    assert decision.allowed is True


def test_untrusted_content_bumps_risk_and_forces_approval(engine):
    decision = engine.evaluate_call(
        make_spec(Risk.MEDIUM), scopes=["files.write"], autonomy=2, tainted=True
    )
    assert decision.allowed is True
    assert decision.risk is Risk.HIGH
    assert decision.requires_approval is True
    assert "untrusted" in decision.reason.lower()


def test_taint_never_lowers_risk(engine):
    assert engine.effective_risk(Risk.LOW, tainted=True) is Risk.MEDIUM
    assert engine.effective_risk(Risk.HIGH, tainted=True) is Risk.CRITICAL
    assert engine.effective_risk(Risk.MEDIUM, tainted=False) is Risk.MEDIUM


def test_plan_scopes_are_narrowed_not_widened(engine):
    assert engine.task_scopes(["system.read"]) == ["system.read"]
    assert engine.task_scopes(["made.up.scope"]) == []
    assert "files.write" in engine.task_scopes(None)


def test_approval_fingerprint_is_exact(engine):
    first = engine.approval_fingerprint("files.delete", {"path": "a.txt"})
    second = engine.approval_fingerprint("files.delete", {"path": "b.txt"})
    assert first != second


async def test_emergency_stop_blocks_everything(runtime):
    engine = PolicyEngine(runtime.config, runtime.store)
    runtime.store.set_emergency_stop(True)
    decision = engine.evaluate_call(make_spec(Risk.LOW), scopes=["files.write"], autonomy=2)
    assert decision.allowed is False
    assert decision.code == "emergency_stop"
    runtime.store.set_emergency_stop(False)
    assert engine.evaluate_call(make_spec(Risk.LOW), scopes=["files.write"], autonomy=2).allowed


def test_plan_evaluation_rejects_unknown_tools(engine):
    from matrix_ai.core.models import Plan, PlanStep, StepKind

    plan = Plan(
        goal="x",
        steps=[
            PlanStep(
                idx=0, title="x", kind=StepKind.TOOL, tool_name="missing.tool", arguments={}
            )
        ],
    )
    decision = engine.evaluate_plan(plan, scopes=["files.write"], autonomy=2, specs={})
    assert decision.allowed is False
    assert decision.code == "unknown_tool"


def test_plan_evaluation_flags_high_risk(engine):
    from matrix_ai.core.models import Plan, PlanStep, StepKind

    spec = make_spec(Risk.HIGH, name="files.delete")
    plan = Plan(
        goal="delete",
        steps=[
            PlanStep(
                idx=0,
                title="delete",
                kind=StepKind.TOOL,
                tool_name="files.delete",
                arguments={"path": "x"},
            )
        ],
    )
    decision = engine.evaluate_plan(
        plan, scopes=["files.write"], autonomy=2, specs={"files.delete": spec}
    )
    assert decision.allowed is True
    assert decision.requires_approval is True
    assert decision.risk is Risk.HIGH



def test_owner_can_opt_in_to_extra_scope_without_defaulting_it(tmp_path):
    config = MatrixConfig(home=tmp_path, additional_scopes=("github.write",))
    engine = PolicyEngine(config)
    assert "github.write" not in engine.task_scopes(None)
    assert "github.write" in engine.task_scopes(["github.write"])
    decision = engine.evaluate_call(
        make_spec(Risk.HIGH, scope="github.write"),
        scopes=engine.task_scopes(["github.write"]),
        autonomy=2,
    )
    assert decision.allowed is True
    assert decision.requires_approval is True


def test_extra_scopes_reject_wildcard_and_malformed_values(tmp_path):
    with pytest.raises(ConfigError, match="MATRIX_EXTRA_SCOPES"):
        MatrixConfig(home=tmp_path, additional_scopes=("*",))
