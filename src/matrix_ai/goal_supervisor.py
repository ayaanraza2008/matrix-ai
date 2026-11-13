"""Persistent, policy-respecting supervisor for multi-task goal DAGs.

Goal nodes are ordinary MATRIX goals/tasks. Dependencies unlock only after the
owner confirms the prerequisite goal. This prevents a model's advisory review
from silently authorising downstream actions.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from .config import MatrixConfig
from .core.goal_graph import GoalGraph, GoalGraphError
from .core.models import parse_dt, utcnow
from .core.orchestrator import Orchestrator
from .storage.store import Store

log = logging.getLogger("matrix.goal_supervisor")
FAILED_STATES = {"partial", "failed", "cancelled", "blocked", "needs_attention"}
ACTIVE_STATES = {"running", "awaiting_verification"}


class GoalSupervisor:
    def __init__(
        self,
        *,
        store: Store,
        orchestrator: Orchestrator,
        config: MatrixConfig,
        poll_seconds: float = 5.0,
        max_concurrent_nodes: int = 2,
    ) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.config = config
        self.poll_seconds = max(1.0, poll_seconds)
        self.max_concurrent_nodes = max(1, min(max_concurrent_nodes, 4))
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="matrix-goal-supervisor")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep supervisor alive; persist an incident
                log.warning("goal supervisor tick failed: %s", type(exc).__name__)
                self.store.audit(
                    "goal_supervisor.tick_failed",
                    actor="goal-supervisor",
                    status="failed",
                    detail={"error": type(exc).__name__},
                )
            await asyncio.sleep(self.poll_seconds)

    async def tick(self) -> list[dict]:
        """Dispatch only dependency-ready nodes; safe to call from API or loop."""
        results: list[dict] = []
        async with self._lock:
            for graph_row in self.store.active_goal_graphs():
                results.extend(await self._tick_graph(graph_row["id"]))
        return results

    async def _tick_graph(self, graph_id: str) -> list[dict]:
        record = self.store.get_goal_graph(graph_id)
        nodes = record["nodes"]
        try:
            graph = GoalGraph.from_mapping(
                {node["node_id"]: node["depends_on"] for node in nodes}
            )
        except GoalGraphError as exc:
            self.store.update_goal_graph_state(graph_id, "failed")
            self.store.audit(
                "goal_graph.invalid",
                actor="goal-supervisor",
                status="failed",
                detail={"graph_id": graph_id, "error": str(exc)[:500]},
            )
            return [{"graph_id": graph_id, "status": "invalid"}]

        deadline = parse_dt(record.get("deadline"))
        if deadline is not None and deadline <= utcnow():
            queued = [node["node_id"] for node in nodes if node["goal"]["state"] == "queued"]
            self.store.block_goal_graph_nodes(graph_id, queued, "goal graph deadline expired")
            for node in nodes:
                goal = node["goal"]
                if goal["task_id"] and goal["state"] in ACTIVE_STATES:
                    await self.orchestrator.cancel(goal["task_id"])
            completed_any = any(node["goal"]["state"] == "verified" for node in nodes)
            terminal_state = "partial" if completed_any else "failed"
            self.store.update_goal_graph_state(graph_id, terminal_state)
            self.store.audit(
                "goal_graph.deadline_expired",
                actor="goal-supervisor",
                status=terminal_state,
                detail={"graph_id": graph_id},
            )
            return [{"graph_id": graph_id, "status": "deadline_expired"}]

        completed = {node["node_id"] for node in nodes if node["goal"]["state"] == "verified"}
        failed = {node["node_id"] for node in nodes if node["goal"]["state"] in FAILED_STATES}
        running = {node["node_id"] for node in nodes if node["goal"]["state"] in ACTIVE_STATES}
        blocked = graph.blocked_nodes(failed)
        newly_blocked = self.store.block_goal_graph_nodes(
            graph_id, list(blocked), "blocked because a prerequisite goal did not succeed"
        )
        if newly_blocked:
            self.store.audit(
                "goal_graph.nodes_blocked",
                actor="goal-supervisor",
                status="blocked",
                detail={"graph_id": graph_id, "nodes": list(blocked)},
            )

        available_slots = max(0, self.max_concurrent_nodes - len(running))
        ready = (
            graph.ready_nodes(
                completed,
                running=running,
                failed=failed,
                limit=min(4, available_slots),
            )
            if available_slots
            else ()
        )
        results: list[dict] = []
        if not self.store.emergency_stop:
            by_id = {node["node_id"]: node for node in nodes}
            for node_id in ready:
                node = by_id[node_id]
                goal = node["goal"]
                remaining = self.config.task_deadline_seconds
                if deadline is not None:
                    remaining = max(1.0, min(remaining, (deadline - datetime.now(UTC)).total_seconds()))
                task_id = None
                try:
                    task_id = self.orchestrator.create_task(
                        request=(
                            f"Goal: {goal['title']}\n"
                            f"Success criteria: {goal['success_criteria']}"
                        ),
                        user_id=record["owner_id"],
                        kind="goal",
                        goal_id=goal["id"],
                        deadline_seconds=remaining,
                    )
                    self.store.attach_goal_task(goal["id"], task_id)
                    await self.orchestrator.enqueue(task_id)
                    self.store.audit(
                        "goal_graph.node_dispatched",
                        actor="goal-supervisor",
                        task_id=task_id,
                        status="running",
                        detail={"graph_id": graph_id, "node_id": node_id, "goal_id": goal["id"]},
                    )
                    results.append({"graph_id": graph_id, "node_id": node_id, "task_id": task_id, "status": "dispatched"})
                except Exception as exc:  # isolate one node failure from independent branches
                    self.store.fail_goal_start(
                        goal["id"],
                        f"Goal node could not be started ({type(exc).__name__}).",
                    )
                    self.store.audit(
                        "goal_graph.node_start_failed",
                        actor="goal-supervisor",
                        task_id=task_id,
                        status="failed",
                        detail={"graph_id": graph_id, "node_id": node_id, "error": type(exc).__name__},
                    )
                    results.append({"graph_id": graph_id, "node_id": node_id, "status": "start_failed"})

        refreshed = self.store.goal_graph_nodes(graph_id)
        states = [node["goal"]["state"] for node in refreshed]
        if states and all(state == "verified" for state in states):
            next_state = "completed"
        elif any(state == "awaiting_verification" for state in states):
            next_state = "awaiting_owner_confirmation"
        elif any(state == "running" for state in states):
            next_state = "running"
        elif any(state == "queued" for state in states):
            next_state = "queued"
        elif any(state in FAILED_STATES for state in states):
            next_state = "partial" if any(state == "verified" for state in states) else "failed"
        else:
            next_state = "failed"
        if record["state"] != next_state:
            self.store.update_goal_graph_state(graph_id, next_state)
            self.store.audit(
                "goal_graph.state_changed",
                actor="goal-supervisor",
                status=next_state,
                detail={"graph_id": graph_id},
            )
        return results


__all__ = ["GoalSupervisor"]
