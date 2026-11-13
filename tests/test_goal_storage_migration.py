from __future__ import annotations

from matrix_ai.storage.db import Database


def test_schema_v4_migrates_goal_node_identity_column_and_unique_index(tmp_path):
    path = tmp_path / "matrix.db"
    database = Database(path)
    database.execute("DROP INDEX IF EXISTS idx_tasks_goal_node")
    database.execute("ALTER TABLE tasks DROP COLUMN goal_node_id")
    database.execute("PRAGMA user_version=4")
    database.close()

    migrated = Database(path)
    columns = {
        row["name"]
        for row in migrated.query("PRAGMA table_info(tasks)")
    }
    indexes = {
        row["name"]
        for row in migrated.query("PRAGMA index_list(tasks)")
    }
    version = migrated.query_one("PRAGMA user_version")[0]

    assert version == 6
    assert "goal_node_id" in columns
    assert "idx_tasks_goal_node" in indexes
    migrated.close()
