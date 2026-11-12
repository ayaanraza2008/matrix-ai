from __future__ import annotations

import pytest
from pydantic import ValidationError

from matrix_ai.core.models import Plan, PlanStep, StepStatus


def test_plan_rejects_unknown_self_duplicate_and_cyclic_dependencies():
    with pytest.raises(ValidationError, match="unknown dependencies"):
        Plan(goal="bad", steps=[PlanStep(idx=0, title="a", depends_on=[2])])

    with pytest.raises(ValidationError, match="itself"):
        Plan(goal="bad", steps=[PlanStep(idx=0, title="a", depends_on=[0])])

    with pytest.raises(ValidationError, match="duplicate dependencies"):
        Plan(
            goal="bad",
            steps=[
                PlanStep(idx=0, title="a", depends_on=[1, 1]),
                PlanStep(idx=1, title="b"),
            ],
        )

    with pytest.raises(ValidationError, match="cycle"):
        Plan(
            goal="bad",
            steps=[
                PlanStep(idx=0, title="a", depends_on=[1]),
                PlanStep(idx=1, title="b", depends_on=[0]),
            ],
        )


def test_plan_rejects_boolean_dependency_indexes():
    with pytest.raises(ValidationError):
        Plan.model_validate(
            {"goal": "bad", "steps": [{"idx": 0, "title": "a", "depends_on": [True]}]}
        )


def test_store_schedules_only_steps_whose_dependencies_completed(runtime):
    plan = Plan(
        goal="reverse dependency order",
        steps=[
            PlanStep(idx=0, title="publish", depends_on=[2]),
            PlanStep(idx=1, title="prepare"),
            PlanStep(idx=2, title="review"),
        ],
    )
    task_id = runtime.orchestrator.create_task(request="dependency test")
    runtime.store.save_plan(task_id, plan)

    assert runtime.store.next_pending_step_index(task_id) == 1
    runtime.store.update_step(task_id, 1, status=StepStatus.COMPLETED.value)
    assert runtime.store.next_pending_step_index(task_id) == 2
    runtime.store.update_step(task_id, 2, status=StepStatus.COMPLETED.value)
    assert runtime.store.next_pending_step_index(task_id) == 0


def test_store_blocks_transitive_descendants_of_failed_step(runtime):
    plan = Plan(
        goal="failure propagation",
        steps=[
            PlanStep(idx=0, title="dependent", depends_on=[1]),
            PlanStep(idx=1, title="failed root"),
            PlanStep(idx=2, title="transitive dependent", depends_on=[0]),
            PlanStep(idx=3, title="independent"),
        ],
    )
    task_id = runtime.orchestrator.create_task(request="dependency failure test")
    runtime.store.save_plan(task_id, plan)
    runtime.store.update_step(task_id, 1, status=StepStatus.FAILED.value, error="provider_failed")

    assert runtime.store.next_pending_step_index(task_id) == 3
    assert runtime.store.step_row(task_id, 0)["status"] == StepStatus.BLOCKED.value
    assert runtime.store.step_row(task_id, 2)["status"] == StepStatus.BLOCKED.value
    assert runtime.store.step_row(task_id, 3)["status"] == StepStatus.PENDING.value


@pytest.mark.asyncio
async def test_orchestrator_continues_independent_branch_after_tool_failure(runtime, monkeypatch):
    plan = Plan(
        goal="continue independent branch",
        steps=[
            PlanStep(idx=0, title="failing prerequisite", tool_name="system.get_status"),
            PlanStep(idx=1, title="independent answer", kind="answer", arguments={"answer": "independent branch completed"}),
            PlanStep(idx=2, title="dependent answer", kind="answer", arguments={"answer": "must not run"}, depends_on=[0]),
        ],
    )

    async def build_plan(_task_id):
        return plan, "dependency_test", "none", [], False, []

    async def fail_tool(_arguments, _ctx):
        raise RuntimeError("simulated tool failure")

    monkeypatch.setattr(runtime.orchestrator, "_build_plan", build_plan)
    monkeypatch.setattr(runtime.registry.get("system.get_status"), "run", fail_tool)
    runtime.config.max_retries = 0

    task_id = runtime.orchestrator.create_task(request="exercise dependency scheduling")
    await runtime.orchestrator.enqueue(task_id)
    runner = runtime.orchestrator._runners[task_id]
    await runner

    task = runtime.store.get_task_row(task_id)
    assert task["state"] == "PARTIAL"
    assert runtime.store.step_row(task_id, 0)["status"] == StepStatus.FAILED.value
    assert runtime.store.step_row(task_id, 1)["status"] == StepStatus.COMPLETED.value
    assert runtime.store.step_row(task_id, 2)["status"] == StepStatus.BLOCKED.value
