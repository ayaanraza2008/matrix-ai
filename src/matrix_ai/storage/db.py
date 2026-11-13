"""SQLite access layer.

SQLite is the source of truth for users, devices, tasks, approvals, tool runs,
audit events and governed memory. WAL mode keeps readers responsive while a
task writes; every mutation is parameterised and bounded.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'owner',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    paired_at TEXT NOT NULL,
    last_seen_at TEXT,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    device_id TEXT,
    scope TEXT NOT NULL DEFAULT 'owner',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS pairing_codes (
    code_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    device_id TEXT,
    kind TEXT NOT NULL DEFAULT 'chat',
    goal_id TEXT,
    state TEXT NOT NULL,
    original_request TEXT NOT NULL,
    normalized_goal TEXT,
    intent TEXT,
    risk_class TEXT,
    autonomy_level INTEGER NOT NULL DEFAULT 2,
    scopes_json TEXT NOT NULL DEFAULT '[]',
    plan_source TEXT,
    degraded INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    approval_id TEXT,
    model TEXT,
    prompt_version TEXT,
    summary TEXT,
    error TEXT,
    evidence_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deadline TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    success_criteria TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    task_id TEXT,
    result_summary TEXT,
    deadline TEXT,
    verification_status TEXT NOT NULL DEFAULT 'pending',
    criteria_status TEXT NOT NULL DEFAULT 'not_evaluated',
    evidence_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_goals_owner_created ON goals(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_goals_state ON goals(state);

CREATE TABLE IF NOT EXISTS plan_steps (
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'tool',
    tool_name TEXT,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    evidence_json TEXT,
    PRIMARY KEY (task_id, idx)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    step_idx INTEGER,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    risk TEXT NOT NULL,
    permission_scope TEXT,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT,
    output_json TEXT,
    error TEXT,
    evidence_json TEXT,
    tainted INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    duration_ms REAL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_task ON tool_calls(task_id);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    step_idx INTEGER,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    action_hash TEXT NOT NULL,
    risk TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    event_type TEXT NOT NULL,
    task_id TEXT,
    tool_name TEXT,
    status TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_events(task_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts DESC);

CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory_items(kind);
CREATE INDEX IF NOT EXISTS idx_memory_user ON memory_items(user_id);

CREATE TABLE IF NOT EXISTS integrations (
    name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    detail TEXT,
    checked_at TEXT
);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    autonomy_level INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run_at TEXT,
    last_run_at TEXT,
    last_status TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_flags (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);
"""


class Database:
    """Thread-safe thin wrapper with explicit transactions."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self.migrate()

    def _configure(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")

    def migrate(self) -> None:
        with self._lock, self._conn:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                self._conn.executescript(SCHEMA_SQL)
                columns = {row[1] for row in self._conn.execute("PRAGMA table_info(tasks)").fetchall()}
                if "goal_id" not in columns:
                    self._conn.execute("ALTER TABLE tasks ADD COLUMN goal_id TEXT")
                goal_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(goals)").fetchall()}
                for name, declaration in (
                    ("deadline", "TEXT"),
                    ("verification_status", "TEXT NOT NULL DEFAULT 'pending'"),
                    ("criteria_status", "TEXT NOT NULL DEFAULT 'not_evaluated'"),
                    ("evidence_json", "TEXT"),
                ):
                    if name not in goal_columns:
                        self._conn.execute(f"ALTER TABLE goals ADD COLUMN {name} {declaration}")
                self._conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_goal ON tasks(goal_id)")
                self._ensure_fts()
                self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _ensure_fts(self) -> None:
        try:
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id UNINDEXED, title, content)"
            )
            self.fts_enabled = True
        except sqlite3.OperationalError:  # pragma: no cover - FTS5 usually present
            self.fts_enabled = False

    # -- primitives -------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, tuple(params))

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        with self._lock, self._conn:
            self._conn.executemany(sql, rows)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # -- maintenance ------------------------------------------------------
    def integrity_check(self) -> str:
        row = self.query_one("PRAGMA integrity_check")
        return str(row[0]) if row else "unknown"

    def backup_to(self, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(str(destination))
            try:
                self._conn.backup(target)
                target.commit()
            finally:
                target.close()
        return destination

    def table_counts(self) -> dict[str, int]:
        tables = ("goals", "tasks", "tool_calls", "audit_events", "memory_items", "devices", "approvals")
        counts: dict[str, int] = {}
        for table in tables:
            row = self.query_one(f"SELECT COUNT(*) AS c FROM {table}")  # noqa: S608 - static names
            counts[table] = int(row["c"]) if row else 0
        return counts

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
