"""The single explicit task state machine.

The model may *propose* a plan; only the orchestrator drives these transitions.
Illegal transitions raise, which makes "the model said it finished" impossible:
the only path to COMPLETE is a validated VERIFY step with evidence.
"""

from __future__ import annotations

from .models import TERMINAL_TASK_STATES, TaskState

ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.RECEIVED: frozenset(
        {TaskState.UNDERSTAND, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.UNDERSTAND: frozenset(
        {TaskState.POLICY_CHECK, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.POLICY_CHECK: frozenset(
        {TaskState.PLAN, TaskState.WAITING_FOR_USER, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.PLAN: frozenset(
        {
            TaskState.APPROVAL_REQUIRED,
            TaskState.TOOL_SELECTION,
            TaskState.COMPLETE,
            TaskState.PARTIAL,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.APPROVAL_REQUIRED: frozenset(
        {
            TaskState.WAITING_FOR_USER,
            TaskState.TOOL_SELECTION,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.WAITING_FOR_USER: frozenset(
        {
            TaskState.TOOL_SELECTION,
            TaskState.EXECUTE,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.TOOL_SELECTION: frozenset(
        {
            TaskState.APPROVAL_REQUIRED,
            TaskState.EXECUTE,
            TaskState.FAILED,
            TaskState.PARTIAL,
            TaskState.CANCELLED,
        }
    ),
    TaskState.EXECUTE: frozenset(
        {TaskState.VERIFY, TaskState.RETRY_SAFE, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.VERIFY: frozenset(
        {
            TaskState.TOOL_SELECTION,
            TaskState.COMPLETE,
            TaskState.PARTIAL,
            TaskState.RETRY_SAFE,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.RETRY_SAFE: frozenset(
        {TaskState.TOOL_SELECTION, TaskState.EXECUTE, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.COMPLETE: frozenset(),
    TaskState.PARTIAL: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}

#: Human-facing status vocabulary required by the UI contract.
STATE_LABELS: dict[TaskState, str] = {
    TaskState.RECEIVED: "Understanding",
    TaskState.UNDERSTAND: "Understanding",
    TaskState.POLICY_CHECK: "Checking policy",
    TaskState.PLAN: "Planning",
    TaskState.APPROVAL_REQUIRED: "Needs approval",
    TaskState.TOOL_SELECTION: "Selecting tool",
    TaskState.EXECUTE: "Executing",
    TaskState.VERIFY: "Verifying",
    TaskState.RETRY_SAFE: "Retrying safely",
    TaskState.WAITING_FOR_USER: "Needs approval",
    TaskState.COMPLETE: "Completed",
    TaskState.PARTIAL: "Partially completed",
    TaskState.FAILED: "Failed",
    TaskState.CANCELLED: "Cancelled",
}


class TaskStateMachine:
    """Pure, side-effect-free transition checker."""

    @staticmethod
    def is_terminal(state: TaskState | str) -> bool:
        try:
            return TaskState(state) in TERMINAL_TASK_STATES
        except ValueError:
            return False

    @staticmethod
    def can_transition(source: TaskState | str, target: TaskState | str) -> bool:
        try:
            return TaskState(target) in ALLOWED_TRANSITIONS[TaskState(source)]
        except (KeyError, ValueError):
            return False

    @staticmethod
    def assert_transition(source: TaskState | str, target: TaskState | str) -> None:
        from ..errors import IllegalTransition

        if not TaskStateMachine.can_transition(source, target):
            raise IllegalTransition(f"illegal task transition {source} -> {target}")

    @staticmethod
    def allowed_next(source: TaskState | str) -> list[str]:
        try:
            return sorted(state.value for state in ALLOWED_TRANSITIONS[TaskState(source)])
        except (KeyError, ValueError):
            return []
