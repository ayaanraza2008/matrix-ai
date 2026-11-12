"""A10 - deleted memory is no longer retrievable."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.acceptance


async def test_deleted_memory_disappears_from_search_and_list(client):
    created = await client.post(
        "/api/memory",
        json={
            "kind": "knowledge",
            "title": "Temporary secret plan",
            "content": "The launch codeword is Zephyr and it must be forgotten.",
            "source": "test",
        },
    )
    assert created.status_code == 201
    item_id = created.json()["id"]

    before = await client.get("/api/memory/search", params={"q": "Zephyr"})
    assert any(entry["id"] == item_id for entry in before.json()["items"])

    deleted = await client.delete(f"/api/memory/{item_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == item_id

    after = await client.get("/api/memory/search", params={"q": "Zephyr"})
    assert all(entry["id"] != item_id for entry in after.json()["items"])

    listed = await client.get("/api/memory")
    assert all(entry["id"] != item_id for entry in listed.json())

    again = await client.delete(f"/api/memory/{item_id}")
    assert again.status_code == 404


async def test_deleted_memory_is_removed_from_full_text_index(client, runtime):
    created = await client.post(
        "/api/memory",
        json={
            "kind": "knowledge",
            "title": "Indexed fact",
            "content": "UniqueTokenForDeletionTest appears exactly once.",
            "source": "test",
        },
    )
    item_id = created.json()["id"]
    row = runtime.store.db.query_one("SELECT COUNT(*) AS c FROM memory_fts WHERE id=?", (item_id,))
    assert row["c"] == 1
    await client.delete(f"/api/memory/{item_id}")
    row = runtime.store.db.query_one("SELECT COUNT(*) AS c FROM memory_fts WHERE id=?", (item_id,))
    assert row["c"] == 0


async def test_memory_update_is_searchable(client):
    created = await client.post(
        "/api/memory",
        json={"kind": "project", "title": "Old title", "content": "Old content here.", "source": "test"},
    )
    item_id = created.json()["id"]
    updated = await client.patch(
        f"/api/memory/{item_id}",
        json={"title": "New title", "content": "Refactored content mentions Hyperion."},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "New title"
    search = await client.get("/api/memory/search", params={"q": "Hyperion"})
    assert [entry["id"] for entry in search.json()["items"]] == [item_id]
