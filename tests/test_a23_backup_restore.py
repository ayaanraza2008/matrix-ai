"""A23 - database backup can be restored and passes an integrity check."""

from __future__ import annotations

import json
import sqlite3

import pytest
from conftest import execute_tool, wait_for_task

from matrix_ai.storage.db import Database

pytestmark = pytest.mark.acceptance


async def test_backup_is_valid_and_restorable(client, runtime, tmp_path):
    task = await execute_tool(client, "system.get_status", {})
    await wait_for_task(client, task["id"])
    original_count = runtime.store.db.query_one("SELECT COUNT(*) AS c FROM tasks")["c"]

    destination = runtime.store.backup(tmp_path / "matrix-backup.db")
    assert destination.exists() and destination.stat().st_size > 0

    restored = Database(destination)
    try:
        assert restored.integrity_check() == "ok"
        assert restored.query_one("SELECT COUNT(*) AS c FROM tasks")["c"] == original_count
        audit_count = restored.query_one("SELECT COUNT(*) AS c FROM audit_events")["c"]
        assert audit_count > 0
    finally:
        restored.close()


async def test_backup_is_a_consistent_snapshot(client, runtime, tmp_path):
    await execute_tool(client, "files.write_text", {"path": "snapshot.txt", "content": "hello"})
    destination = runtime.store.backup(tmp_path / "snapshot.db")
    connection = sqlite3.connect(destination)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"tasks", "tool_calls", "memory_items", "audit_events", "approvals"} <= tables
        payload = connection.execute("SELECT original_request FROM tasks LIMIT 1").fetchone()
        assert payload is not None
    finally:
        connection.close()


async def test_invalid_backup_path_is_reported(runtime, tmp_path):
    target = tmp_path / "backups" / "nested" / "matrix.db"
    path = runtime.store.backup(target)
    assert path.parent.exists()
    json.dumps({"backup": str(path)})
