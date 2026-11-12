"""Validated dependency graphs for bounded autonomous goal plans.

This module is deliberately side-effect free. It validates planner-produced
DAGs before any task is persisted or dispatched; execution and permissions
remain owned by the orchestrator.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Mapping

MAX_GOAL_NODES = 64
MAX_DEPENDENCIES_PER_NODE = 16
MAX_PARALLEL_READY = 4


class GoalGraphError(ValueError):
    """A goal plan is malformed, unsafe to schedule, or exceeds its bounds."""


@dataclass(frozen=True, slots=True)
class GoalNode:
    node_id: str
    depends_on: tuple[str, ...] = ()


class GoalGraph:
    """Immutable, deterministic DAG with bounded node and fan-in counts."""

    def __init__(self, nodes: Iterable[GoalNode], *, max_nodes: int = MAX_GOAL_NODES) -> None:
        if not 1 <= max_nodes <= MAX_GOAL_NODES:
            raise GoalGraphError(f"max_nodes must be between 1 and {MAX_GOAL_NODES}")
        materialized = tuple(nodes)
        if not materialized:
            raise GoalGraphError("a goal graph must contain at least one node")
        if len(materialized) > max_nodes:
            raise GoalGraphError(f"goal graph exceeds the {max_nodes}-node limit")

        by_id: dict[str, GoalNode] = {}
        for node in materialized:
            if not isinstance(node.node_id, str) or not node.node_id or len(node.node_id) > 128:
                raise GoalGraphError("node IDs must be non-empty strings of at most 128 characters")
            if node.node_id in by_id:
                raise GoalGraphError(f"duplicate node ID: {node.node_id}")
            if len(node.depends_on) > MAX_DEPENDENCIES_PER_NODE:
                raise GoalGraphError(f"node {node.node_id} exceeds the dependency limit")
            if len(set(node.depends_on)) != len(node.depends_on):
                raise GoalGraphError(f"node {node.node_id} has duplicate dependencies")
            if node.node_id in node.depends_on:
                raise GoalGraphError(f"node {node.node_id} cannot depend on itself")
            by_id[node.node_id] = GoalNode(node.node_id, tuple(node.depends_on))

        for node in by_id.values():
            missing = set(node.depends_on) - by_id.keys()
            if missing:
                raise GoalGraphError(
                    f"node {node.node_id} references unknown dependencies: {sorted(missing)}"
                )

        self._nodes: Mapping[str, GoalNode] = by_id
        self._topological_order = self._sort()

    @classmethod
    def from_mapping(cls, specification: Mapping[str, Iterable[str]]) -> "GoalGraph":
        if not isinstance(specification, Mapping):
            raise GoalGraphError("goal graph must be a mapping of node IDs to dependencies")
        return cls(GoalNode(node_id, tuple(dependencies)) for node_id, dependencies in specification.items())

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(self._topological_order)

    @property
    def topological_order(self) -> tuple[str, ...]:
        return tuple(self._topological_order)

    def ready_nodes(
        self,
        completed: Iterable[str],
        *,
        running: Iterable[str] = (),
        failed: Iterable[str] = (),
        limit: int = MAX_PARALLEL_READY,
    ) -> tuple[str, ...]:
        """Return stable ready nodes; never unlock descendants of failed nodes."""
        if not 1 <= limit <= MAX_PARALLEL_READY:
            raise GoalGraphError(f"ready-node limit must be between 1 and {MAX_PARALLEL_READY}")
        completed_set = set(completed)
        running_set = set(running)
        failed_set = set(failed)
        known = self._nodes.keys()
        if not completed_set <= known or not running_set <= known or not failed_set <= known:
            raise GoalGraphError("execution state contains a node outside this goal graph")
        if completed_set & (running_set | failed_set) or running_set & failed_set:
            raise GoalGraphError("a node cannot be in multiple execution states")
        blocked = set(failed_set)
        changed = True
        while changed:
            changed = False
            for node in self._nodes.values():
                if node.node_id not in blocked and set(node.depends_on) & blocked:
                    blocked.add(node.node_id)
                    changed = True
        selected = []
        for node_id in self._topological_order:
            node = self._nodes[node_id]
            if node_id in completed_set | running_set | blocked:
                continue
            if set(node.depends_on) <= completed_set:
                selected.append(node_id)
                if len(selected) == limit:
                    break
        return tuple(selected)

    def _sort(self) -> list[str]:
        indegree = {node_id: len(node.depends_on) for node_id, node in self._nodes.items()}
        dependents: dict[str, list[str]] = {node_id: [] for node_id in self._nodes}
        for node in self._nodes.values():
            for dependency in node.depends_on:
                dependents[dependency].append(node.node_id)
        ready = deque(node_id for node_id in self._nodes if indegree[node_id] == 0)
        order: list[str] = []
        while ready:
            node_id = ready.popleft()
            order.append(node_id)
            for dependent in dependents[node_id]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
        if len(order) != len(self._nodes):
            cyclic = sorted(node_id for node_id, degree in indegree.items() if degree)
            raise GoalGraphError(f"goal dependency cycle detected: {cyclic}")
        return order
