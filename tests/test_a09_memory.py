"""A09 - approved memory retrieval returns provenance."""

from __future__ import annotations

import json

import pytest
from conftest import execute_tool, wait_for_task

pytestmark = pytest.mark.acceptance


async def test_memory_roundtrip_with_provenance(client):
    created = await client.post(
        "/api/memory",
        json={
            "kind": "project",
            "title": "MATRIX stack",
            "content": "MATRIX uses FastAPI, SQLite and pydantic for the core assistant.",
            "source": "owner-note",
            "confidence": 0.9,
        },
    )
    assert created.status_code == 201
    item = created.json()
    assert item["source"] == "owner-note"
    assert item["confidence"] == 0.9

    search = await client.get("/api/memory/search", params={"q": "SQLite"})
    assert search.status_code == 200
    payload = search.json()
    assert payload["count"] >= 1
    match = next(entry for entry in payload["items"] if entry["id"] == item["id"])
    assert match["source"] == "owner-note"
    assert match["confidence"] == 0.9
    assert match["created_at"]
    assert "score" in match


async def test_memory_save_tool_is_policy_checked_and_persisted(client):
    task = await execute_tool(
        client,
        "memory.save",
        {
            "title": "Workspace rule",
            "content": "All generated files stay inside the MATRIX workspace.",
            "kind": "preference",
            "confidence": 1.0,
        },
    )
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "COMPLETE", done["error"]

    search = await client.get("/api/memory/search", params={"q": "workspace"})
    titles = [entry["title"] for entry in search.json()["items"]]
    assert "Workspace rule" in titles


async def test_memory_search_tool_returns_items(client):
    await client.post(
        "/api/memory",
        json={
            "kind": "knowledge",
            "title": "Preferred language",
            "content": "The owner prefers concise English answers.",
            "source": "owner",
        },
    )
    task = await execute_tool(client, "memory.search", {"query": "concise English", "limit": 5})
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "COMPLETE"
    output = done["tool_calls"][-1]["output"]
    assert output["count"] >= 1
    assert output["items"][0]["source"] == "owner"


async def test_memory_refuses_secrets(client):
    response = await client.post(
        "/api/memory",
        json={
            "kind": "knowledge",
            "title": "API credential",
            "content": "api_key = " + "abcdefgh12345678",
            "source": "user",
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


async def test_memory_export_is_json(client):
    response = await client.get("/api/memory/export")
    assert response.status_code == 200
    payload = response.json()
    assert "items" in payload
    json.dumps(payload)  # must be JSON-serialisable
