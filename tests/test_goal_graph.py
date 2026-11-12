from __future__ import annotations

import pytest

from matrix_ai.core.goal_graph import (
    MAX_GOAL_NODES,
    GoalGraph,
    GoalGraphError,
    GoalNode,
)


def test_goal_graph_orders_dependencies_and_parallel_ready_nodes():
    graph = GoalGraph(
        [
            GoalNode("publish", ("render", "review")),
            GoalNode("render"),
            GoalNode("review", ("render",)),
            GoalNode("prepare"),
        ]
    )
    assert graph.topological_order == ("render", "prepare", "review", "publish")
    assert graph.ready_nodes([]) == ("render", "prepare")
    assert graph.ready_nodes(["render"]) == ("prepare", "review")
    assert graph.ready_nodes(["render", "prepare", "review"]) == ("publish",)


def test_goal_graph_rejects_cycles():
    with pytest.raises(GoalGraphError, match="cycle"):
        GoalGraph([GoalNode("a", ("b",)), GoalNode("b", ("a",))])


def test_goal_graph_rejects_unknown_duplicate_and_self_dependencies():
    with pytest.raises(GoalGraphError, match="unknown"):
        GoalGraph([GoalNode("a", ("missing",))])
    with pytest.raises(GoalGraphError, match="duplicate dependencies"):
        GoalGraph([GoalNode("a", ("b", "b")), GoalNode("b")])
    with pytest.raises(GoalGraphError, match="itself"):
        GoalGraph([GoalNode("a", ("a",))])


def test_goal_graph_bounds_size_and_fan_in():
    with pytest.raises(GoalGraphError, match="node limit"):
        GoalGraph([GoalNode(str(index)) for index in range(MAX_GOAL_NODES + 1)])
    with pytest.raises(GoalGraphError, match="dependency limit"):
        GoalGraph([GoalNode("root")] + [
            GoalNode(f"child-{index}") for index in range(17)
        ] + [GoalNode("join", tuple(f"child-{index}" for index in range(17)))])


def test_ready_nodes_do_not_run_until_dependencies_complete_or_after_failure():
    graph = GoalGraph([
        GoalNode("a"),
        GoalNode("b", ("a",)),
        GoalNode("c", ("b",)),
        GoalNode("independent"),
    ])
    assert graph.ready_nodes([], running=["a"]) == ("independent",)
    assert graph.ready_nodes([], failed=["a"]) == ("independent",)
    assert graph.ready_nodes(["a"]) == ("independent", "b")
    assert graph.ready_nodes(["a", "independent"], limit=1) == ("b",)
    with pytest.raises(GoalGraphError, match="multiple execution states"):
        graph.ready_nodes(["a"], running=["a"])


def test_ready_nodes_reject_invalid_limits_and_foreign_state():
    graph = GoalGraph([GoalNode("a")])
    with pytest.raises(GoalGraphError, match="limit"):
        graph.ready_nodes([], limit=5)
    with pytest.raises(GoalGraphError, match="outside"):
        graph.ready_nodes(["foreign"])



def test_goal_graph_rejects_string_as_dependency_sequence_and_invalid_ids():
    with pytest.raises(GoalGraphError, match="sequence"):
        GoalGraph.from_mapping({"a": "bc", "b": [], "c": []})
    with pytest.raises(GoalGraphError, match="invalid dependency"):
        GoalGraph([GoalNode("a", (" ",))])
    with pytest.raises(GoalGraphError, match="node IDs"):
        GoalGraph([GoalNode("   ")])
