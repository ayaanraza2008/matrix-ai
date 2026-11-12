"""Task state machine invariants."""

from __future__ import annotations

import pytest

from matrix_ai.core.models import TaskState
from matrix_ai.core.state_machine import STATE_LABELS, TaskStateMachine
from matrix_ai.errors import IllegalTransition


def test_happy_path_is_legal():
    path = [
        TaskState.RECEIVED,
        TaskState.UNDERSTAND,
        TaskState.POLICY_CHECK,
        TaskState.PLAN,
        TaskState.TOOL_SELECTION,
        TaskState.EXECUTE,
        TaskState.VERIFY,
        TaskState.COMPLETE,
    ]
    for source, target in zip(path, path[1:], strict=False):
        assert TaskStateMachine.can_transition(source, target), f"{source} -> {target}"


def test_verification_is_mandatory_before_complete():
    assert not TaskStateMachine.can_transition(TaskState.EXECUTE, TaskState.COMPLETE)
    assert TaskStateMachine.can_transition(TaskState.VERIFY, TaskState.COMPLETE)


def test_terminal_states_have_no_exits():
    for state in (TaskState.COMPLETE, TaskState.PARTIAL, TaskState.FAILED, TaskState.CANCELLED):
        assert TaskStateMachine.is_terminal(state)
        assert TaskStateMachine.allowed_next(state) == []


def test_illegal_transition_raises():
    with pytest.raises(IllegalTransition):
        TaskStateMachine.assert_transition(TaskState.COMPLETE, TaskState.EXECUTE)


def test_any_non_terminal_state_can_fail_safely():
    non_terminal = [state for state in TaskState if not TaskStateMachine.is_terminal(state)]
    for state in non_terminal:
        assert TaskStateMachine.can_transition(state, TaskState.FAILED), state


def test_every_state_has_a_ui_label():
    for state in TaskState:
        assert state in STATE_LABELS


def test_unknown_state_is_not_terminal():
    assert TaskStateMachine.is_terminal("NOT_A_STATE") is False
    assert TaskStateMachine.can_transition("NOT_A_STATE", TaskState.PLAN) is False
