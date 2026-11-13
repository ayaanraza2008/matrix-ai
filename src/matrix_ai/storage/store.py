"""Task, approval, audit, integration and job persistence.

This is the only module that runs task SQL. The orchestrator talks to these
methods, so "what state is the task in" has exactly one source of truth.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..core.models import (
    ApprovalStatus,
    ApprovalView,
    AuditEventView,
    CallStatus,
    IntegrationView,
    JobView,
    Plan,
    Risk,
    StepKind,
    StepStatus,
    StepView,
    TaskState,
    TaskView,
    ToolCallView,
    canonical_json,
    iso,
    parse_dt,
    utcnow,
)
from ..errors import NotFoundError
from ..security.redaction import redact_args, redact_obj, redact_text
from .db import Database

TASK_COLUMNS = {
    "state",
    "normalized_goal",
    "intent",
    "risk_class",
    "autonomy_level",
    "scopes_json",
    "plan_source",
    "degraded",
    "retry_count",
    "cancel_requested",
    "approval_id",
    "model",
    "prompt_version",
    "summary",
    "error",
    "evidence_json",
    "deadline",
    "finished_at",
    "kind",
    "device_id",
    "goal_id",
    "goal_node_id",
}


def _json_load(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except ValueError:
        return fallback


class Store:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- identity ---------------------------------------------------------
    def ensure_user(self, user_id: str, display_name: str = "Owner", role: str = "owner") -> None:
        self.db.execute(
            "INSERT INTO users(id, display_name, role, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO NOTHING",
            (user_id, display_name, role, iso()),
        )

    # -- audit ------------------------------------------------------------
    def audit(
        self,
        event_type: str,
        *,
        actor: str = "system",
        task_id: str | None = None,
        tool_name: str | None = None,
        status: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        safe_detail = redact_obj(detail or {})
        self.db.execute(
            "INSERT INTO audit_events(ts, actor, event_type, task_id, tool_name, status, detail_json) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                iso(),
                actor,
                event_type,
                task_id,
                tool_name,
                status,
                json.dumps(safe_detail, default=str)[:8000],
            ),
        )

    def list_audit(self, *, limit: int = 100, task_id: str | None = None) -> list[AuditEventView]:
        limit = max(1, min(limit, 500))
        if task_id:
            rows = self.db.query(
                "SELECT * FROM audit_events WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit)
            )
        else:
            rows = self.db.query("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,))
        return [
            AuditEventView(
                id=row["id"],
                ts=row["ts"],
                actor=row["actor"],
                event_type=row["event_type"],
                task_id=row["task_id"],
                tool_name=row["tool_name"],
                status=row["status"],
                detail=_json_load(row["detail_json"], {}),
            )
            for row in rows
        ]

    # -- tasks ------------------------------------------------------------
    def create_task(
        self,
        *,
        user_id: str,
        original_request: str,
        device_id: str | None = None,
        autonomy_level: int = 2,
        scopes: list[str] | None = None,
        kind: str = "chat",
        deadline_seconds: float | None = None,
        goal_id: str | None = None,
        goal_node_id: str | None = None,
        idempotency_scope: str | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        if bool(idempotency_scope) != bool(idempotency_key):
            raise ValueError("idempotency scope and key must be supplied together")
        if idempotency_scope and (
            len(idempotency_scope) > 100 or len(idempotency_key or "") > 512
        ):
            raise ValueError("idempotency scope and key exceed their limits")
        task_id = uuid.uuid4().hex
        now = iso()
        deadline = iso(utcnow() + timedelta(seconds=deadline_seconds)) if deadline_seconds else None
        with self.db.transaction() as conn:
            if idempotency_scope and idempotency_key:
                existing = conn.execute(
                    "SELECT result_json FROM idempotency WHERE scope=? AND key=?",
                    (idempotency_scope, idempotency_key),
                ).fetchone()
                if existing is not None:
                    result = json.loads(existing["result_json"] or "{}")
                    existing_task_id = result.get("task_id") if isinstance(result, dict) else None
                    if not isinstance(existing_task_id, str) or not existing_task_id:
                        raise ValueError("stored idempotency result is invalid")
                    return existing_task_id
            conn.execute(
                "INSERT INTO tasks(id, user_id, device_id, kind, goal_id, goal_node_id, state, original_request, autonomy_level, "
                "scopes_json, created_at, updated_at, deadline) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    user_id,
                    device_id,
                    kind,
                    goal_id,
                    goal_node_id,
                    TaskState.RECEIVED.value,
                    original_request,
                    int(autonomy_level),
                    canonical_json(scopes or []),
                    now,
                    now,
                    deadline,
                ),
            )
            if idempotency_scope and idempotency_key:
                conn.execute(
                    "INSERT INTO idempotency(scope, key, result_json, created_at) VALUES(?,?,?,?)",
                    (idempotency_scope, idempotency_key, canonical_json({"task_id": task_id}), now),
                )
        return task_id

    def get_idempotent_task_id(self, scope: str, key: str) -> str | None:
        """Return the task previously created for an idempotency key, if any."""
        row = self.db.query_one(
            "SELECT result_json FROM idempotency WHERE scope=? AND key=?", (scope, key)
        )
        if row is None:
            return None
        result = json.loads(row["result_json"] or "{}")
        task_id = result.get("task_id") if isinstance(result, dict) else None
        return task_id if isinstance(task_id, str) and task_id else None

    def is_idempotent_task(self, scope: str, task_id: str) -> bool:
        """Return whether a task belongs to a specific idempotency scope."""
        row = self.db.query_one(
            "SELECT 1 FROM idempotency WHERE scope=? AND result_json=? LIMIT 1",
            (scope, canonical_json({"task_id": task_id})),
        )
        return row is not None

    def list_idempotent_task_ids(self, scope: str, *, limit: int = 20) -> list[str]:
        """List recent task IDs created through one idempotency scope."""
        limit = max(1, min(int(limit), 500))
        rows = self.db.query(
            "SELECT result_json FROM idempotency WHERE scope=? ORDER BY created_at DESC LIMIT ?",
            (scope, limit),
        )
        task_ids: list[str] = []
        seen: set[str] = set()
        for row in rows:
            result = _json_load(row["result_json"], {})
            task_id = result.get("task_id") if isinstance(result, dict) else None
            if isinstance(task_id, str) and task_id and task_id not in seen:
                task_ids.append(task_id)
                seen.add(task_id)
        return task_ids

    def get_system_flag(self, name: str, default: str | None = None) -> str | None:
        """Read a small durable runtime cursor or setting."""
        row = self.db.query_one("SELECT value FROM system_flags WHERE name=?", (name,))
        return str(row["value"]) if row is not None else default

    def set_system_flag(self, name: str, value: str) -> None:
        """Persist a small durable runtime cursor or setting."""
        if not name or len(name) > 128 or len(value) > 512:
            raise ValueError("system flag name and value must be non-empty and bounded")
        self.db.execute(
            "INSERT INTO system_flags(name, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (name, value, iso()),
        )

    def claim_idempotency(self, scope: str, key: str) -> bool:
        """Atomically claim an external delivery ID so webhook retries do not duplicate tasks."""
        if not scope or not key or len(scope) > 100 or len(key) > 512:
            raise ValueError("idempotency scope and key must be non-empty and bounded")
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO idempotency(scope, key, result_json, created_at) VALUES(?,?,?,?)",
                (scope, key, "{}", iso()),
            )
            return cursor.rowcount == 1

    def release_idempotency(self, scope: str, key: str) -> None:
        self.db.execute("DELETE FROM idempotency WHERE scope=? AND key=?", (scope, key))

    def get_task_row(self, task_id: str):
        row = self.db.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if row is None:
            raise NotFoundError(f"task {task_id} not found")
        return row

    def update_task(self, task_id: str, **fields: Any) -> None:
        unknown = set(fields) - TASK_COLUMNS
        if unknown:
            raise ValueError(f"unknown task fields: {sorted(unknown)}")
        self.get_task_row(task_id)
        if "scopes_json" in fields and not isinstance(fields["scopes_json"], str):
            fields["scopes_json"] = canonical_json(fields["scopes_json"])
        assignments = ", ".join(f"{column}=?" for column in fields)
        params = [fields[column] for column in fields]
        self.db.execute(
            f"UPDATE tasks SET {assignments}, updated_at=? WHERE id=?",  # noqa: S608 - columns are whitelisted
            (*params, iso(), task_id),
        )

    def transition(self, task_id: str, target: TaskState, *, summary: str | None = None) -> None:
        from ..core.state_machine import TaskStateMachine

        row = self.get_task_row(task_id)
        TaskStateMachine.assert_transition(row["state"], target)
        fields: dict[str, Any] = {"state": target.value}
        if summary is not None:
            fields["summary"] = summary
        if TaskStateMachine.is_terminal(target):
            fields["finished_at"] = iso()
        self.update_task(task_id, **fields)

    def set_cancel_requested(self, task_id: str) -> None:
        self.update_task(task_id, cancel_requested=1)

    def is_cancel_requested(self, task_id: str) -> bool:
        row = self.get_task_row(task_id)
        return bool(row["cancel_requested"])

    def list_tasks(self, *, limit: int = 50, state: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        if state:
            rows = self.db.query(
                "SELECT * FROM tasks WHERE state=? ORDER BY created_at DESC LIMIT ?", (state, limit)
            )
        else:
            rows = self.db.query("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._task_summary(row) for row in rows]

    def nonterminal_tasks(self) -> list[Any]:
        """Return every task that may still need startup recovery."""
        return self.db.query(
            "SELECT * FROM tasks WHERE state NOT IN (?,?,?,?) ORDER BY created_at",
            (
                TaskState.COMPLETE.value,
                TaskState.PARTIAL.value,
                TaskState.FAILED.value,
                TaskState.CANCELLED.value,
            ),
        )

    def _task_summary(self, row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "state": row["state"],
            "kind": row["kind"],
            "goal_id": row["goal_id"],
            "original_request": row["original_request"],
            "normalized_goal": row["normalized_goal"],
            "risk": row["risk_class"],
            "summary": row["summary"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "autonomy_level": row["autonomy_level"],
            "plan_source": row["plan_source"],
            "degraded": bool(row["degraded"]),
        }

    # -- durable goals ----------------------------------------------------
    def create_goal(
        self, *, owner_id: str, title: str, success_criteria: str,
        deadline_seconds: float | None = None,
    ) -> str:
        goal_id = uuid.uuid4().hex
        now = iso()
        deadline = iso(utcnow() + timedelta(seconds=deadline_seconds)) if deadline_seconds else None
        self.db.execute(
            "INSERT INTO goals(id, owner_id, title, success_criteria, state, deadline, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (goal_id, owner_id, title, success_criteria, "queued", deadline, now, now),
        )
        return goal_id

    def get_goal(self, goal_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM goals WHERE id=?", (goal_id,))
        if row is None:
            raise NotFoundError(f"goal {goal_id} not found")
        result = dict(row)
        result["task"] = self._task_summary(self.get_task_row(row["task_id"])) if row["task_id"] else None
        return result

    def confirm_goal_criteria(
        self, goal_id: str, *, owner_id: str, outcome: str, reason: str
    ) -> dict[str, Any]:
        """Persist the owner's final decision; model assessment is never final authority."""
        goal = self.get_goal(goal_id)
        if goal["owner_id"] != owner_id:
            raise NotFoundError(f"goal {goal_id} not found")
        if goal["state"] != "awaiting_verification":
            raise ValueError("only goals awaiting verification can be owner-confirmed")
        if outcome not in {"met", "not_met"}:
            raise ValueError("outcome must be met or not_met")
        if len(reason.strip()) < 8:
            raise ValueError("confirmation reason must contain at least 8 characters")
        final_state = "completed" if outcome == "met" else "partial"
        criteria_status = "owner_confirmed_met" if outcome == "met" else "owner_confirmed_not_met"
        cursor = self.db.execute(
            "UPDATE goals SET state=?, verification_status='owner_confirmed', criteria_status=?, "
            "criteria_confirmed_at=?, criteria_confirmed_by=?, criteria_confirmation_reason=?, "
            "updated_at=?, finished_at=? "
            "WHERE id=? AND owner_id=? AND state='awaiting_verification'",
            (
                final_state, criteria_status, iso(), owner_id, reason.strip()[:500],
                iso(), iso(), goal_id, owner_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("goal changed before confirmation; reload and retry")
        return self.get_goal(goal_id)

    def list_goals(self, *, owner_id: str, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        rows = self.db.query(
            "SELECT * FROM goals WHERE owner_id=? ORDER BY created_at DESC LIMIT ?",
            (owner_id, limit),
        )
        return [self.get_goal(row["id"]) for row in rows]

    def attach_goal_task(self, goal_id: str, task_id: str) -> None:
        task = self.get_task_row(task_id)
        goal = self.get_goal(goal_id)
        if task["goal_id"] != goal_id:
            raise ValueError("task is not linked to this goal")
        if task["user_id"] != goal["owner_id"]:
            raise ValueError("task owner does not match goal owner")
        cursor = self.db.execute(
            "UPDATE goals SET task_id=?, deadline=(SELECT deadline FROM tasks WHERE id=?), "
            "state='running', updated_at=? WHERE id=? AND task_id IS NULL AND state='queued'",
            (task_id, task_id, iso(), goal_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("goal is already attached to a task or is not queued")

    def fail_goal_start(self, goal_id: str, summary: str) -> None:
        self.db.execute(
            "UPDATE goals SET state='failed', result_summary=?, updated_at=?, finished_at=? "
            "WHERE id=? AND task_id IS NULL AND state='queued'",
            (summary[:1000], iso(), iso(), goal_id),
        )

    def sync_goal_for_task(self, task_id: str) -> None:
        task = self.get_task_row(task_id)
        goal_id = task["goal_id"]
        if not goal_id:
            return
        state = {
            TaskState.COMPLETE.value: "awaiting_verification",
            TaskState.PARTIAL.value: "partial",
            TaskState.FAILED.value: "failed",
            TaskState.CANCELLED.value: "cancelled",
        }.get(task["state"])
        if state is None:
            return
        try:
            evidence = json.loads(task["evidence_json"] or "{}")
        except (TypeError, ValueError):
            evidence = {}
        verified = bool(evidence.get("verified")) and state == "awaiting_verification"
        verification_status = "task_steps_verified" if verified else (
            "task_steps_not_verified" if state in {"partial", "failed"} else "not_applicable"
        )
        self.db.execute(
            "UPDATE goals SET state=?, result_summary=?, verification_status=?, "
            "criteria_status='not_evaluated', evidence_json=?, updated_at=?, finished_at=? "
            "WHERE id=? AND task_id=?",
            (
                state, task["summary"] or task["error"], verification_status,
                json.dumps(evidence), iso(), iso(), goal_id, task_id,
            ),
        )

    def save_goal_graph(self, goal_id: str, nodes: list[dict[str, Any]]) -> None:
        """Persist a validated DAG as a plan; this does not dispatch any work."""
        goal = self.get_goal(goal_id)
        if goal["task_id"] is not None or goal["state"] not in {"queued", "planned"}:
            raise ValueError("a goal graph can only be changed before task execution starts")
        now = iso()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM goal_nodes WHERE goal_id=?", (goal_id,))
            for node in nodes:
                conn.execute(
                    "INSERT INTO goal_nodes(goal_id, node_id, title, depends_on_json, state, created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        goal_id, node["node_id"], node["title"],
                        canonical_json(node["depends_on"]), "pending", now, now,
                    ),
                )
            conn.execute(
                "UPDATE goals SET state='planned', updated_at=? WHERE id=?",
                (now, goal_id),
            )

    def get_goal_graph(self, goal_id: str) -> list[dict[str, Any]]:
        self.get_goal(goal_id)
        rows = self.db.query(
            "SELECT node_id, title, depends_on_json, state, task_id, attempt_count, error, evidence_json "
            "FROM goal_nodes WHERE goal_id=? ORDER BY rowid",
            (goal_id,),
        )
        result = []
        for row in rows:
            result.append({
                "node_id": row["node_id"],
                "title": row["title"],
                "depends_on": _json_load(row["depends_on_json"], []),
                "state": row["state"],
                "task_id": row["task_id"],
                "attempt_count": row["attempt_count"],
                "error": row["error"],
                "evidence": _json_load(row["evidence_json"], None),
            })
        return result
    def start_goal_graph(self, goal_id: str) -> None:
        goal = self.get_goal(goal_id)
        if goal["state"] not in {"planned", "running"}:
            raise ValueError("only a planned or interrupted running goal can be started")
        if not self.get_goal_graph(goal_id):
            raise ValueError("goal has no persisted execution plan")
        self.db.execute(
            "UPDATE goals SET state='running', updated_at=? WHERE id=? AND state IN ('planned','running')",
            (iso(), goal_id),
        )

    def pause_goal_graph(self, goal_id: str) -> bool:
        """Atomically close the dispatch gate without interrupting active node tasks."""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT state, task_id FROM goals WHERE id=?", (goal_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"goal {goal_id} not found")
            if row["task_id"] is not None or row["state"] not in {"planned", "running"}:
                return False
            cursor = conn.execute(
                "UPDATE goals SET state='paused', updated_at=? "
                "WHERE id=? AND task_id IS NULL AND state IN ('planned','running')",
                (iso(), goal_id),
            )
            return cursor.rowcount == 1

    def resume_goal_graph(self, goal_id: str) -> bool:
        """Re-open dispatch only for a goal explicitly paused by its owner."""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT state, task_id FROM goals WHERE id=?", (goal_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"goal {goal_id} not found")
            if row["task_id"] is not None or row["state"] != "paused":
                return False
            cursor = conn.execute(
                "UPDATE goals SET state='running', updated_at=? "
                "WHERE id=? AND task_id IS NULL AND state='paused'",
                (iso(), goal_id),
            )
            return cursor.rowcount == 1

    def claim_goal_node(self, goal_id: str, node_id: str, *, limit: int = 4) -> bool:
        """Atomically reserve one dependency-ready node within the per-goal concurrency bound."""
        from ..core.goal_graph import MAX_PARALLEL_READY

        if not 1 <= limit <= MAX_PARALLEL_READY:
            raise ValueError(f"limit must be between 1 and {MAX_PARALLEL_READY}")
        with self.db.transaction() as conn:
            goal = conn.execute("SELECT state FROM goals WHERE id=?", (goal_id,)).fetchone()
            if goal is None or goal["state"] not in {"planned", "running"}:
                return False
            rows = conn.execute(
                "SELECT node_id, depends_on_json, state FROM goal_nodes WHERE goal_id=?",
                (goal_id,),
            ).fetchall()
            states = {row["node_id"]: row["state"] for row in rows}
            target = next((row for row in rows if row["node_id"] == node_id), None)
            if target is None or target["state"] != "pending":
                return False
            active = sum(state in {"running", "dispatching"} for state in states.values())
            if active >= limit:
                return False
            dependencies = _json_load(target["depends_on_json"], [])
            if any(states.get(dep) != "completed" for dep in dependencies):
                return False
            cursor = conn.execute(
                "UPDATE goal_nodes SET state='dispatching', attempt_count=attempt_count+1, "
                "updated_at=? WHERE goal_id=? AND node_id=? AND state='pending'",
                (iso(), goal_id, node_id),
            )
            if cursor.rowcount != 1:
                return False
            conn.execute(
                "UPDATE goals SET state='running', updated_at=? WHERE id=? AND state IN ('planned','running')",
                (iso(), goal_id),
            )
            return True

    def attach_goal_node_task(self, goal_id: str, node_id: str, task_id: str) -> None:
        """Idempotently link the unique durable task for this goal node."""
        task = self.get_task_row(task_id)
        goal = self.get_goal(goal_id)
        if (
            task["user_id"] != goal["owner_id"]
            or task["kind"] != "goal_node"
            or task["goal_id"] != goal_id
            or task["goal_node_id"] != node_id
        ):
            raise ValueError("goal node task owner or identity does not match")
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT state, task_id FROM goal_nodes WHERE goal_id=? AND node_id=?",
                (goal_id, node_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"goal node {node_id} not found")
            if row["task_id"] not in (None, task_id):
                raise ValueError("goal node is already linked to another task")
            if row["state"] not in {"dispatching", "running"}:
                raise ValueError("goal node is not in a dispatchable state")
            conn.execute(
                "UPDATE goal_nodes SET task_id=?, state='running', updated_at=? "
                "WHERE goal_id=? AND node_id=?",
                (task_id, iso(), goal_id, node_id),
            )
            conn.execute(
                "UPDATE goals SET state='running', updated_at=? WHERE id=? AND state IN ('planned','running')",
                (iso(), goal_id),
            )

    def get_goal_node_task(self, goal_id: str, node_id: str):
        return self.db.query_one(
            "SELECT * FROM tasks WHERE goal_id=? AND goal_node_id=?",
            (goal_id, node_id),
        )

    def reconcile_goal_node_tasks(self) -> int:
        """Repair the task-created/node-linked crash window using the unique node identity."""
        rows = self.db.query(
            "SELECT id, goal_id, goal_node_id FROM tasks "
            "WHERE goal_id IS NOT NULL AND goal_node_id IS NOT NULL"
        )
        repaired = 0
        with self.db.transaction() as conn:
            for task in rows:
                cursor = conn.execute(
                    "UPDATE goal_nodes SET task_id=?, state='running', updated_at=? "
                    "WHERE goal_id=? AND node_id=? AND task_id IS NULL "
                    "AND state IN ('pending','dispatching','running')",
                    (task["id"], iso(), task["goal_id"], task["goal_node_id"]),
                )
                repaired += cursor.rowcount
        return repaired

    def fail_goal_node_dispatch(self, goal_id: str, node_id: str, error: str) -> None:
        self.db.execute(
            "UPDATE goal_nodes SET state='failed', error=?, updated_at=? "
            "WHERE goal_id=? AND node_id=? AND state='dispatching' AND task_id IS NULL",
            (error[:1000], iso(), goal_id, node_id),
        )

    def update_goal_node(
        self, goal_id: str, node_id: str, *, state: str, error: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        if state not in {"pending", "dispatching", "running", "completed", "failed", "blocked", "cancelled"}:
            raise ValueError("invalid goal node state")
        self.db.execute(
            "UPDATE goal_nodes SET state=?, error=?, evidence_json=?, updated_at=? "
            "WHERE goal_id=? AND node_id=?",
            (state, error[:1000] if error else None, canonical_json(evidence) if evidence else None,
             iso(), goal_id, node_id),
        )

    def recover_goal_graphs(self) -> list[dict[str, Any]]:
        self.reconcile_goal_node_tasks()
        rows = self.db.query(
            "SELECT id, owner_id, state FROM goals WHERE state IN ('planned','running') "
            "AND task_id IS NULL ORDER BY created_at"
        )
        return [{"id": row["id"], "owner_id": row["owner_id"], "state": row["state"]} for row in rows]

    def get_goal_node_for_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT goal_id, node_id FROM goal_nodes WHERE task_id=?",
            (task_id,),
        )
        return {"goal_id": row["goal_id"], "node_id": row["node_id"]} if row else None

    def reset_orphaned_goal_node_claims(self, goal_id: str) -> int:
        cursor = self.db.execute(
            "UPDATE goal_nodes SET state='pending', updated_at=? "
            "WHERE goal_id=? AND state='dispatching' AND task_id IS NULL",
            (iso(), goal_id),
        )
        return cursor.rowcount

    def cancelling_goal_ids(self) -> list[str]:
        rows = self.db.query("SELECT id FROM goals WHERE state='cancelling' ORDER BY updated_at")
        return [str(row["id"]) for row in rows]

    def goal_node_task_ids(self, goal_id: str) -> list[str]:
        rows = self.db.query(
            "SELECT id FROM tasks WHERE goal_id=? AND goal_node_id IS NOT NULL "
            "AND state NOT IN ('COMPLETE','PARTIAL','FAILED','CANCELLED')",
            (goal_id,),
        )
        return [str(row["id"]) for row in rows]

    def finish_cancel_goal_graph(self, goal_id: str) -> None:
        now = iso()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE goal_nodes SET state='cancelled', error=COALESCE(error,'goal cancelled by owner'), updated_at=? "
                "WHERE goal_id=? AND state IN ('pending','dispatching','running')",
                (now, goal_id),
            )
            conn.execute(
                "UPDATE goals SET state='cancelled', result_summary='Goal cancelled by owner.', "
                "verification_status='not_applicable', updated_at=?, finished_at=? WHERE id=? AND task_id IS NULL",
                (now, now, goal_id),
            )

    def begin_cancel_goal_graph(self, goal_id: str) -> list[str]:
        """Make a goal non-dispatchable before cancelling any live node tasks."""
        self.reconcile_goal_node_tasks()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE goals SET state='cancelling', updated_at=? "
                "WHERE id=? AND state IN ('planned','running','paused') AND task_id IS NULL",
                (iso(), goal_id),
            )
            conn.execute(
                "UPDATE goal_nodes SET state='cancelled', error='goal cancelled by owner', updated_at=? "
                "WHERE goal_id=? AND state IN ('pending','dispatching')",
                (iso(), goal_id),
            )
            rows = conn.execute(
                "SELECT task_id FROM goal_nodes WHERE goal_id=? AND state='running' AND task_id IS NOT NULL",
                (goal_id,),
            ).fetchall()
        return [str(row["task_id"]) for row in rows]

    def finish_goal_graph(self, goal_id: str, *, state: str, summary: str) -> None:
        if state not in {"completed", "awaiting_verification", "partial", "failed", "cancelled"}:
            raise ValueError("invalid terminal goal state")
        nodes = self.get_goal_graph(goal_id)
        if not nodes:
            raise ValueError("goal has no execution nodes")
        all_completed = all(node["state"] == "completed" for node in nodes)
        if state in {"completed", "awaiting_verification"} and not all_completed:
            raise ValueError("goal cannot complete or await verification until every node completes")
        self.db.execute(
            "UPDATE goals SET state=?, result_summary=?, verification_status=?, "
            "criteria_status=?, updated_at=?, finished_at=? WHERE id=? AND task_id IS NULL",
            (state, summary[:1000],
             "second_pass_model_verified" if state == "completed" else ("pending_owner_review" if all_completed else "not_verified"),
             "verified_by_model" if state == "completed" else ("not_met" if state == "partial" and all_completed else "not_evaluated"),
             iso(), iso(), goal_id),
        )


    # -- plans & steps ----------------------------------------------------
    def save_plan(self, task_id: str, plan: Plan) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM plan_steps WHERE task_id=?", (task_id,))
            for step in plan.steps:
                conn.execute(
                    "INSERT INTO plan_steps(task_id, idx, title, kind, tool_name, arguments_json, "
                    "depends_on_json, status) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        task_id,
                        step.idx,
                        step.title,
                        step.kind.value,
                        step.tool_name,
                        canonical_json(step.arguments),
                        canonical_json(step.depends_on),
                        StepStatus.PENDING.value,
                    ),
                )

    def steps_for(self, task_id: str) -> list[StepView]:
        rows = self.db.query("SELECT * FROM plan_steps WHERE task_id=? ORDER BY idx", (task_id,))
        steps: list[StepView] = []
        for row in rows:
            steps.append(
                StepView(
                    idx=row["idx"],
                    title=row["title"],
                    kind=StepKind(row["kind"]),
                    tool_name=row["tool_name"],
                    status=StepStatus(row["status"]),
                    arguments=_json_load(row["arguments_json"], {}),
                    depends_on=_json_load(row["depends_on_json"], []),
                    error=row["error"],
                )
            )
        return steps

    def step_row(self, task_id: str, idx: int):
        row = self.db.query_one("SELECT * FROM plan_steps WHERE task_id=? AND idx=?", (task_id, idx))
        if row is None:
            raise NotFoundError(f"step {idx} of task {task_id} not found")
        return row

    def update_step(self, task_id: str, idx: int, **fields: Any) -> None:
        allowed = {"status", "error", "started_at", "finished_at", "evidence_json"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown step fields: {sorted(unknown)}")
        self.step_row(task_id, idx)
        assignments = ", ".join(f"{column}=?" for column in fields)
        self.db.execute(
            f"UPDATE plan_steps SET {assignments} WHERE task_id=? AND idx=?",  # noqa: S608 - whitelisted
            (*fields.values(), task_id, idx),
        )

    def next_pending_step_index(self, task_id: str) -> int | None:
        """Return the lowest-index step whose dependencies have all completed.

        Failed dependencies block their transitive descendants. Tool retries
        happen inside the orchestrator before a step is marked failed; a
        terminally failed step is never replayed here.
        """
        rows = self.db.query(
            "SELECT idx, status, depends_on_json FROM plan_steps WHERE task_id=? ORDER BY idx",
            (task_id,),
        )
        if not rows:
            return None

        states = {int(row["idx"]): str(row["status"]) for row in rows}
        dependencies = {
            int(row["idx"]): _json_load(row["depends_on_json"], [])
            for row in rows
        }

        # Propagate dependency failure before selecting the next runnable step.
        changed = True
        while changed:
            changed = False
            failed = {
                idx for idx, status in states.items()
                if status in {StepStatus.FAILED.value, StepStatus.BLOCKED.value}
            }
            for idx, deps in dependencies.items():
                if states[idx] == StepStatus.PENDING.value and any(dep in failed for dep in deps):
                    self.update_step(
                        task_id,
                        idx,
                        status=StepStatus.BLOCKED.value,
                        error="dependency_failed",
                        finished_at=iso(),
                    )
                    states[idx] = StepStatus.BLOCKED.value
                    changed = True

        for idx in sorted(states):
            if states[idx] != StepStatus.PENDING.value:
                continue
            if all(states.get(dep) == StepStatus.COMPLETED.value for dep in dependencies[idx]):
                return idx
        return None

    def pending_step_count(self, task_id: str) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS c FROM plan_steps WHERE task_id=? AND status IN ('pending','running')",
            (task_id,),
        )
        return int(row["c"]) if row else 0

    # -- tool calls -------------------------------------------------------
    def start_tool_call(
        self,
        *,
        task_id: str,
        step_idx: int | None,
        tool_name: str,
        arguments: dict[str, Any],
        risk: Risk,
        permission_scope: str | None,
        attempt: int = 1,
        tainted: bool = False,
        idempotency_key: str | None = None,
    ) -> str:
        call_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO tool_calls(id, task_id, step_idx, tool_name, arguments_json, risk, "
            "permission_scope, status, attempt, idempotency_key, tainted, started_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                call_id,
                task_id,
                step_idx,
                tool_name,
                canonical_json(redact_args(arguments)),
                risk.value,
                permission_scope,
                CallStatus.RUNNING.value,
                int(attempt),
                idempotency_key,
                1 if tainted else 0,
                iso(),
            ),
        )
        return call_id

    def finish_tool_call(
        self,
        call_id: str,
        *,
        status: CallStatus,
        output: dict[str, Any] | None = None,
        error: str | None = None,
        evidence: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> None:
        self.db.execute(
            "UPDATE tool_calls SET status=?, output_json=?, error=?, evidence_json=?, duration_ms=?, "
            "finished_at=? WHERE id=?",
            (
                status.value,
                json.dumps(redact_obj(output or {}), default=str)[:200_000],
                redact_text(error, max_length=2000) if error else None,
                json.dumps(evidence, default=str) if evidence else None,
                duration_ms,
                iso(),
                call_id,
            ),
        )

    def tool_calls_for(self, task_id: str) -> list[ToolCallView]:
        rows = self.db.query("SELECT * FROM tool_calls WHERE task_id=? ORDER BY started_at, id", (task_id,))
        calls: list[ToolCallView] = []
        for row in rows:
            calls.append(
                ToolCallView(
                    id=row["id"],
                    tool_name=row["tool_name"],
                    step_idx=row["step_idx"],
                    status=CallStatus(row["status"]),
                    attempt=row["attempt"],
                    risk=Risk(row["risk"]),
                    output=_json_load(row["output_json"], {}),
                    error=row["error"],
                    evidence=_json_load(row["evidence_json"], None),
                    duration_ms=row["duration_ms"] or 0.0,
                    started_at=row["started_at"],
                    finished_at=row["finished_at"],
                )
            )
        return calls

    # -- approvals --------------------------------------------------------
    def create_approval(
        self,
        *,
        task_id: str,
        step_idx: int | None,
        tool_name: str,
        arguments: dict[str, Any],
        action_hash: str,
        risk: Risk,
        ttl_seconds: int,
    ) -> str:
        approval_id = uuid.uuid4().hex
        now = utcnow()
        self.db.execute(
            "INSERT INTO approvals(id, task_id, step_idx, tool_name, arguments_json, action_hash, risk, "
            "status, created_at, expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                approval_id,
                task_id,
                step_idx,
                tool_name,
                canonical_json(redact_args(arguments)),
                action_hash,
                risk.value,
                ApprovalStatus.PENDING.value,
                iso(now),
                iso(now + timedelta(seconds=ttl_seconds)),
            ),
        )
        self.update_task(task_id, approval_id=approval_id)
        return approval_id

    def get_approval_row(self, approval_id: str):
        row = self.db.query_one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if row is None:
            raise NotFoundError(f"approval {approval_id} not found")
        return row

    def pending_approval(self, task_id: str):
        return self.db.query_one(
            "SELECT * FROM approvals WHERE task_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        )

    def decide_approval(
        self, approval_id: str, *, approved: bool, decided_by: str = "owner", reason: str | None = None
    ) -> str:
        row = self.get_approval_row(approval_id)
        if row["status"] != ApprovalStatus.PENDING.value:
            return row["status"]
        expiry = parse_dt(row["expires_at"])
        status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
        if expiry and expiry < utcnow():
            status = ApprovalStatus.EXPIRED
        self.db.execute(
            "UPDATE approvals SET status=?, decided_at=?, decided_by=?, reason=? WHERE id=?",
            (status.value, iso(), decided_by, reason, approval_id),
        )
        return status.value

    def approval_view(self, row) -> ApprovalView:
        return ApprovalView(
            id=row["id"],
            task_id=row["task_id"],
            step_idx=row["step_idx"],
            tool_name=row["tool_name"],
            arguments=_json_load(row["arguments_json"], {}),
            risk=Risk(row["risk"]),
            status=ApprovalStatus(row["status"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            decided_at=row["decided_at"],
            reason=row["reason"],
        )

    def approvals_for(self, task_id: str) -> list[ApprovalView]:
        rows = self.db.query("SELECT * FROM approvals WHERE task_id=? ORDER BY created_at", (task_id,))
        return [self.approval_view(row) for row in rows]

    def approved_approval(self, task_id: str, step_idx: int | None, action_hash: str):
        """An exact, already-approved binding for this action (resume support)."""
        return self.db.query_one(
            "SELECT * FROM approvals WHERE task_id=? AND step_idx IS ? AND action_hash=? "
            "AND status='approved' ORDER BY decided_at DESC LIMIT 1",
            (task_id, step_idx, action_hash),
        )

    # -- untrusted-content taint ------------------------------------------
    def mark_tool_call_tainted(self, call_id: str) -> None:
        self.db.execute("UPDATE tool_calls SET tainted=1 WHERE id=?", (call_id,))

    def has_suspicious_output(self, task_id: str, before_step: int | None = None) -> bool:
        if before_step is None:
            rows = self.db.query(
                "SELECT output_json FROM tool_calls WHERE task_id=? AND tainted=1 LIMIT 1", (task_id,)
            )
        else:
            rows = self.db.query(
                "SELECT output_json FROM tool_calls WHERE task_id=? AND tainted=1 AND step_idx < ? LIMIT 1",
                (task_id, before_step),
            )
        return bool(rows)

    def fail_running_tool_calls(self, task_id: str, message: str) -> int:
        """Close tool calls interrupted by a process exit; never imply they were rolled back."""
        cursor = self.db.execute(
            "UPDATE tool_calls SET status=?, error=?, finished_at=? "
            "WHERE task_id=? AND status=?",
            (
                CallStatus.FAILED.value,
                message[:2000],
                iso(),
                task_id,
                CallStatus.RUNNING.value,
            ),
        )
        return int(cursor.rowcount)

    def fail_running_steps(self, task_id: str, message: str) -> None:
        self.db.execute(
            "UPDATE plan_steps SET status='failed', error=?, finished_at=? "
            "WHERE task_id=? AND status='running'",
            (message[:500], iso(), task_id),
        )

    # -- task view --------------------------------------------------------
    def task_view(self, task_id: str) -> TaskView:
        row = self.get_task_row(task_id)
        return TaskView(
            id=row["id"],
            state=TaskState(row["state"]),
            original_request=row["original_request"],
            normalized_goal=row["normalized_goal"],
            risk=Risk(row["risk_class"]) if row["risk_class"] else None,
            autonomy_level=row["autonomy_level"],
            scopes=_json_load(row["scopes_json"], []),
            summary=row["summary"],
            error=row["error"],
            plan_source=row["plan_source"],
            degraded=bool(row["degraded"]),
            retry_count=row["retry_count"],
            cancel_requested=bool(row["cancel_requested"]),
            model=row["model"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            evidence=_json_load(row["evidence_json"], None),
            steps=self.steps_for(task_id),
            tool_calls=self.tool_calls_for(task_id),
            approvals=self.approvals_for(task_id),
        )

    # -- in-app notification inbox ---------------------------------------
    def create_notification(
        self, *, title: str, message: str, severity: str = "info", event_type: str = "system"
    ) -> str:
        if severity not in {"info", "success", "warning", "critical"}:
            raise ValueError("invalid notification severity")
        preferences = self.notification_preferences()
        if event_type == "health.recovered" and not preferences["health_recovered"]:
            return ""
        if event_type == "daily.digest" and not preferences["daily_digest"]:
            return ""
        # Emergency-stop events are never suppressible by owner preferences.
        notification_id = uuid.uuid4().hex
        raw = self.get_flag("notification_inbox", "[]")
        items = _json_load(raw, [])
        if not isinstance(items, list):
            items = []
        items.append({
            "id": notification_id,
            "created_at": iso(),
            "title": redact_text(title, max_length=160),
            "message": redact_text(message, max_length=1000),
            "severity": severity,
            "event_type": redact_text(event_type, max_length=80),
            "read": False,
        })
        # Bounded storage prevents the inbox flag from growing without limit.
        self.set_flag("notification_inbox", json.dumps(items[-100:], separators=(",", ":")))
        return notification_id

    def notification_preferences(self) -> dict[str, bool]:
        defaults = {"health_recovered": True, "daily_digest": True}
        raw = _json_load(self.get_flag("notification_preferences", "{}"), {})
        if not isinstance(raw, dict):
            raw = {}
        return {key: bool(raw.get(key, default)) for key, default in defaults.items()}

    def set_notification_preferences(self, preferences: dict[str, bool]) -> dict[str, bool]:
        allowed = {"health_recovered", "daily_digest"}
        if set(preferences) - allowed or any(not isinstance(value, bool) for value in preferences.values()):
            raise ValueError("invalid notification preferences")
        current = self.notification_preferences()
        current.update(preferences)
        self.set_flag("notification_preferences", json.dumps(current, separators=(",", ":")))
        return current

    def list_notifications(self, *, limit: int = 50, unread_only: bool = False) -> list[dict[str, Any]]:
        items = _json_load(self.get_flag("notification_inbox", "[]"), [])
        if not isinstance(items, list):
            return []
        items = [item for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)]
        if unread_only:
            items = [item for item in items if not item.get("read", False)]
        return list(reversed(items[-max(1, min(limit, 100)):]))

    def mark_notification_read(self, notification_id: str) -> bool:
        items = _json_load(self.get_flag("notification_inbox", "[]"), [])
        if not isinstance(items, list):
            items = []
        found = False
        for item in items:
            if isinstance(item, dict) and item.get("id") == notification_id:
                item["read"] = True
                found = True
                break
        if not found:
            raise NotFoundError(f"notification {notification_id} not found")
        self.set_flag("notification_inbox", json.dumps(items[-100:], separators=(",", ":")))
        return True

    # -- flags ------------------------------------------------------------
    def set_flag(self, name: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO system_flags(name, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (name, value, iso()),
        )

    def get_flag(self, name: str, default: str | None = None) -> str | None:
        row = self.db.query_one("SELECT value FROM system_flags WHERE name=?", (name,))
        return row["value"] if row else default

    @property
    def emergency_stop(self) -> bool:
        return self.get_flag("emergency_stop", "off") == "on"

    def set_emergency_stop(self, active: bool, *, actor: str = "owner") -> bool:
        self.set_flag("emergency_stop", "on" if active else "off")
        self.audit(
            "security.emergency_stop",
            actor=actor,
            status="active" if active else "cleared",
            detail={"active": active},
        )
        return active

    # -- integrations -----------------------------------------------------
    def upsert_integration(self, name: str, status: str, detail: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO integrations(name, status, detail, checked_at) VALUES(?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET status=excluded.status, detail=excluded.detail, "
            "checked_at=excluded.checked_at",
            (name, status, detail, iso()),
        )

    def integration_rows(self) -> dict[str, Any]:
        return {row["name"]: row for row in self.db.query("SELECT * FROM integrations")}

    def integration_view(self, view: IntegrationView) -> IntegrationView:
        row = self.db.query_one("SELECT * FROM integrations WHERE name=?", (view.name,))
        if row:
            return view.model_copy(
                update={"status": row["status"], "detail": row["detail"], "checked_at": row["checked_at"]}
            )
        return view

    # -- scheduled jobs ---------------------------------------------------
    def create_job(
        self, *, name: str, prompt: str, interval_seconds: int, autonomy_level: int, enabled: bool
    ) -> str:
        job_id = uuid.uuid4().hex
        now = utcnow()
        self.db.execute(
            "INSERT INTO scheduled_jobs(id, name, prompt, interval_seconds, autonomy_level, enabled, "
            "next_run_at, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                job_id,
                name,
                prompt,
                interval_seconds,
                autonomy_level,
                1 if enabled else 0,
                iso(now + timedelta(seconds=interval_seconds)),
                iso(now),
            ),
        )
        return job_id

    def list_jobs(self) -> list[JobView]:
        rows = self.db.query("SELECT * FROM scheduled_jobs ORDER BY created_at")
        return [
            JobView(
                id=row["id"],
                name=row["name"],
                prompt=row["prompt"],
                interval_seconds=row["interval_seconds"],
                autonomy_level=row["autonomy_level"],
                enabled=bool(row["enabled"]),
                next_run_at=row["next_run_at"],
                last_run_at=row["last_run_at"],
                last_status=row["last_status"],
            )
            for row in rows
        ]

    def due_jobs(self, now: Any = None) -> list[JobView]:
        now = now or utcnow()
        return [
            job
            for job in self.list_jobs()
            if job.enabled and job.next_run_at and (parse_dt(job.next_run_at) or now) <= now
        ]

    def mark_job_run(self, job_id: str, *, status: str, interval_seconds: int) -> None:
        now = utcnow()
        self.db.execute(
            "UPDATE scheduled_jobs SET last_run_at=?, last_status=?, next_run_at=? WHERE id=?",
            (iso(now), status, iso(now + timedelta(seconds=interval_seconds)), job_id),
        )

    def set_job_enabled(self, job_id: str, enabled: bool) -> None:
        self.update_job(job_id, enabled=enabled)

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {"name", "prompt", "interval_seconds", "autonomy_level", "enabled"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown job fields: {sorted(unknown)}")
        if not fields:
            raise ValueError("at least one job field must be supplied")
        row = self.db.query_one("SELECT id, interval_seconds FROM scheduled_jobs WHERE id=?", (job_id,))
        if row is None:
            raise NotFoundError(f"job {job_id} not found")
        updates = dict(fields)
        if "enabled" in updates:
            updates["enabled"] = 1 if updates["enabled"] else 0
        if "interval_seconds" in updates or updates.get("enabled") == 1:
            interval = int(updates.get("interval_seconds", row["interval_seconds"]))
            updates["next_run_at"] = iso(utcnow() + timedelta(seconds=interval))
        assignments = ", ".join(f"{column}=?" for column in updates)
        self.db.execute(
            f"UPDATE scheduled_jobs SET {assignments} WHERE id=?",  # noqa: S608 - columns are whitelisted
            (*updates.values(), job_id),
        )

    def delete_job(self, job_id: str) -> None:
        cursor = self.db.execute("DELETE FROM scheduled_jobs WHERE id=?", (job_id,))
        if cursor.rowcount == 0:
            raise NotFoundError(f"job {job_id} not found")

    # -- idempotency ------------------------------------------------------
    def remember_idempotent(self, scope: str, key: str, result: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO idempotency(scope, key, result_json, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(scope, key) DO UPDATE SET result_json=excluded.result_json",
            (scope, key, json.dumps(redact_obj(result), default=str)[:200_000], iso()),
        )

    def recall_idempotent(self, scope: str, key: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT result_json FROM idempotency WHERE scope=? AND key=?", (scope, key))
        return _json_load(row["result_json"], None) if row else None

    # -- maintenance ------------------------------------------------------
    def backup(self, destination: Path) -> Path:
        return self.db.backup_to(destination)

    def stats(self) -> dict[str, Any]:
        counts = self.db.table_counts()
        return {"counts": counts, "integrity": self.db.integrity_check()}

