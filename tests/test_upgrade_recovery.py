"""Upgrade recovery: a failed schema migration must roll back cleanly and retry idempotently."""

from __future__ import annotations

import sqlite3

import pytest

from matrix_ai.storage.db import SCHEMA_VERSION, Database
from matrix_ai.storage.store import Store


def test_failed_upgrade_rolls_back_and_next_open_completes_idempotently(tmp_path, monkeypatch):
    path = tmp_path / "matrix.db"
    db = Database(path)
    store = Store(db)
    try:
        store.create_task(user_id="owner", original_request="recovery probe")
        task_count_before = store.db.query_one("SELECT COUNT(*) AS c FROM tasks")["c"]
        assert task_count_before == 1
    finally:
        db.close()

    # Fail mid-upgrade. The schema script itself is written idempotently; the
    # transactional remainder (ALTERs, indexes, FTS, version bump) must roll
    # back, leaving the database intact and unversioned. user_version is reset
    # to 0 to simulate an installed database from before the current schema.
    downgrade = sqlite3.connect(path)
    try:
        downgrade.execute("PRAGMA user_version=0")
        downgrade.commit()
    finally:
        downgrade.close()

    def broken_fts(self):
        raise RuntimeError("simulated crash during upgrade")

    monkeypatch.setattr(Database, "_ensure_fts", broken_fts)
    with pytest.raises(RuntimeError, match="simulated crash"):
        Database(path)
    monkeypatch.undo()

    # The database must still be intact, data preserved, version not advanced.
    check = sqlite3.connect(path)
    try:
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert check.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION
        rows = check.execute("SELECT original_request FROM tasks").fetchall()
        assert len(rows) == 1 and rows[0][0] == "recovery probe"
    finally:
        check.close()

    # Reopening completes the upgrade idempotently without losing data.
    recovered = Database(path)
    recovered_store = Store(recovered)
    try:
        assert recovered.integrity_check() == "ok"
        assert recovered.query_one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
        goal_columns = {
            row[1] for row in recovered._conn.execute("PRAGMA table_info(goals)").fetchall()
        }
        assert "verification_status" in goal_columns
        assert recovered_store.db.query_one("SELECT COUNT(*) AS c FROM tasks")["c"] == 1
    finally:
        recovered.close()
