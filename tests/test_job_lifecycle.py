from __future__ import annotations

import pytest
from pydantic import ValidationError

from matrix_ai.core.models import JobCreate, JobUpdate
from matrix_ai.errors import NotFoundError
from matrix_ai.storage.db import Database
from matrix_ai.storage.store import Store


def make_store(tmp_path):
    db = Database(tmp_path / "matrix.db")
    return db, Store(db)


def test_owner_goal_can_be_updated_paused_resumed_and_deleted(tmp_path):
    db, store = make_store(tmp_path)
    try:
        job_id = store.create_job(
            name="Daily review",
            prompt="Review the approved workspace",
            interval_seconds=3600,
            autonomy_level=1,
            enabled=True,
        )

        store.update_job(job_id, name="Morning review", prompt="Review approved tasks")
        updated = next(job for job in store.list_jobs() if job.id == job_id)
        assert updated.name == "Morning review"
        assert updated.prompt == "Review approved tasks"

        store.set_job_enabled(job_id, False)
        assert not next(job for job in store.list_jobs() if job.id == job_id).enabled

        store.set_job_enabled(job_id, True)
        resumed = next(job for job in store.list_jobs() if job.id == job_id)
        assert resumed.enabled
        assert resumed.next_run_at is not None

        store.update_job(job_id, interval_seconds=7200, autonomy_level=2)
        updated = next(job for job in store.list_jobs() if job.id == job_id)
        assert updated.interval_seconds == 7200
        assert updated.autonomy_level == 2

        store.delete_job(job_id)
        assert all(job.id != job_id for job in store.list_jobs())
        with pytest.raises(NotFoundError):
            store.delete_job(job_id)
    finally:
        db.close()


def test_goal_update_rejects_empty_null_and_blank_text():
    with pytest.raises(ValidationError):
        JobUpdate()
    with pytest.raises(ValidationError):
        JobUpdate(name=None)
    with pytest.raises(ValidationError):
        JobUpdate(name="   ")


def test_goal_create_keeps_autonomy_bounded():
    with pytest.raises(ValidationError):
        JobCreate(
            name="Unsafe goal",
            prompt="Do everything",
            interval_seconds=3600,
            autonomy_level=3,
        )
