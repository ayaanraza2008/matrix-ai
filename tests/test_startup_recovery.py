"""Startup recovery never loses approval state or blindly replays side effects."""

from __future__ import annotations

import pytest
from conftest import create_workspace_file, execute_tool, wait_for_state, wait_for_task

from matrix_ai.core.models import CallStatus, Plan, PlanStep, StepStatus, TaskState, iso

pytestmark = pytest.mark.acceptance


async def test_received_task_is_requeued_after_restart(client, runtime):
    task_id = runtime.orchestrator.create_direct_tool_task("system.get_status", {})

    recovered = await runtime.orchestrator.recover_after_restart()
    done = await wait_for_task(client, task_id)

    assert recovered["resumed"] == 1
    assert done["state"] == "COMPLETE"
    assert done["evidence"]["verified"] is True


async def test_interrupted_tool_call_is_failed_and_never_replayed(runtime):
    tool = runtime.registry.get("system.get_status")
    spec = tool.spec()
    task_id = runtime.orchestrator.create_direct_tool_task("system.get_status", {})
    runtime.store.save_plan(
        task_id,
        Plan(
            goal="Read system status",
            steps=[PlanStep(idx=0, title="Read system status", tool_name="system.get_status")],
        ),
    )
    runtime.store.update_task(task_id, state=TaskState.EXECUTE.value)
    runtime.store.update_step(
        task_id,
        0,
        status=StepStatus.RUNNING.value,
        started_at=iso(),
    )
    runtime.store.start_tool_call(
        task_id=task_id,
        step_idx=0,
        tool_name=spec.name,
        arguments={},
        risk=spec.risk,
        permission_scope=spec.permission_scope,
    )

    recovered = await runtime.orchestrator.recover_after_restart()
    view = runtime.store.task_view(task_id)
    calls = runtime.store.tool_calls_for(task_id)

    assert recovered["failed"] == 1
    assert view.state is TaskState.FAILED
    assert len(calls) == 1
    assert calls[0].status == CallStatus.FAILED
    assert "not automatically retried" in calls[0].error
    assert view.steps[0].status == StepStatus.FAILED


async def test_pending_approval_is_preserved_across_recovery(client, runtime):
    await create_workspace_file(client, "restart-pending.txt", "keep this file")
    task = await execute_tool(client, "files.delete", {"path": "restart-pending.txt"})
    waiting = await wait_for_state(client, task["id"], {"WAITING_FOR_USER"})

    recovered = await runtime.orchestrator.recover_after_restart()
    current = runtime.store.task_view(task["id"])

    assert recovered["awaiting_approval"] == 1
    assert current.state is TaskState.WAITING_FOR_USER
    assert current.approvals[-1].status.value == "pending"
    assert (runtime.config.workspace_dir / "restart-pending.txt").exists()
    assert not current.tool_calls


async def test_approved_exact_action_resumes_after_restart(client, runtime):
    await create_workspace_file(client, "restart-approved.txt", "delete only after approval")
    tool = runtime.registry.get("files.delete")
    validated = tool.validate({"path": "restart-approved.txt"})
    spec = tool.spec()
    task_id = runtime.orchestrator.create_direct_tool_task(
        "files.delete", validated.model_dump(mode="json")
    )
    runtime.store.save_plan(
        task_id,
        Plan(
            goal="Delete the approved file",
            steps=[
                PlanStep(
                    idx=0,
                    title="Delete approved file",
                    tool_name="files.delete",
                    arguments=validated.model_dump(mode="json"),
                )
            ],
        ),
    )
    action_hash = runtime.policy.approval_fingerprint(
        spec.name, validated.model_dump(mode="json")
    )
    approval_id = runtime.store.create_approval(
        task_id=task_id,
        step_idx=0,
        tool_name=spec.name,
        arguments=validated.model_dump(mode="json"),
        action_hash=action_hash,
        risk=spec.risk,
        ttl_seconds=120,
    )
    assert runtime.store.decide_approval(approval_id, approved=True) == "approved"
    runtime.store.update_task(task_id, state=TaskState.WAITING_FOR_USER.value)

    recovered = await runtime.orchestrator.recover_after_restart()
    done = await wait_for_task(client, task_id)

    assert recovered["resumed"] == 1
    assert done["state"] == "COMPLETE"
    assert not (runtime.config.workspace_dir / "restart-approved.txt").exists()
    assert done["approvals"][-1]["status"] == "approved"


async def test_expired_approval_fails_without_running_action(client, runtime):
    await create_workspace_file(client, "restart-expired.txt", "must remain")
    tool = runtime.registry.get("files.delete")
    validated = tool.validate({"path": "restart-expired.txt"})
    spec = tool.spec()
    task_id = runtime.orchestrator.create_direct_tool_task(
        "files.delete", validated.model_dump(mode="json")
    )
    runtime.store.save_plan(
        task_id,
        Plan(
            goal="Delete the expired approval test file",
            steps=[
                PlanStep(
                    idx=0,
                    title="Delete test file",
                    tool_name="files.delete",
                    arguments=validated.model_dump(mode="json"),
                )
            ],
        ),
    )
    action_hash = runtime.policy.approval_fingerprint(
        spec.name, validated.model_dump(mode="json")
    )
    runtime.store.create_approval(
        task_id=task_id,
        step_idx=0,
        tool_name=spec.name,
        arguments=validated.model_dump(mode="json"),
        action_hash=action_hash,
        risk=spec.risk,
        ttl_seconds=-5,
    )
    runtime.store.update_task(task_id, state=TaskState.WAITING_FOR_USER.value)

    recovered = await runtime.orchestrator.recover_after_restart()
    view = runtime.store.task_view(task_id)

    assert recovered["failed"] == 1
    assert view.state is TaskState.FAILED
    assert view.approvals[-1].status.value == "expired"
    assert (runtime.config.workspace_dir / "restart-expired.txt").exists()
    assert not view.tool_calls
