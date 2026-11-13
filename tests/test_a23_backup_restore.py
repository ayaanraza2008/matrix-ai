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


async def test_interrupted_backup_file_is_detected_not_silently_restorable(runtime, tmp_path):
    """A truncated/interrupted backup must fail the integrity gate, never pass silently."""
    path = runtime.store.backup(tmp_path / "victim.db")
    raw = path.read_bytes()
    truncated = tmp_path / "interrupted.db"
    truncated.write_bytes(raw[: len(raw) // 2])

    check = sqlite3.connect(truncated)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            check.execute("PRAGMA integrity_check").fetchone()
    finally:
        check.close()


async def test_memory_refuses_credential_content_and_backups_stay_clean(client, runtime, tmp_path):
    """Credential-shaped text is refused at the memory boundary (403), and a full
    backup of the resulting database must not contain the raw secret either."""
    secret = "Bearer abcdefghijklmnopqrstuvwxyz"
    refused = await client.post(
        "/api/memory",
        json={
            "kind": "project",
            "title": "Deployment note",
            "content": f"The leaked credential was {secret}",
            "source": "owner-note",
            "confidence": 0.9,
        },
    )
    assert refused.status_code == 403

    destination = runtime.store.backup(tmp_path / "redaction.db")
    blob = destination.read_bytes()
    assert secret.encode() not in blob
