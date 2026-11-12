"""A01 - fresh startup, clear health state, no secret leakage."""

from __future__ import annotations

import pytest
from conftest import TEST_TOKEN

pytestmark = pytest.mark.acceptance


async def test_health_is_public_and_honest(anon_client):
    response = await anon_client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "degraded"}
    assert payload["database"] == "ok"
    assert payload["model"]["configured"] is False
    assert payload["model"]["status"] == "unconfigured"
    assert payload["emergency_stop"] is False
    assert payload["uptime_seconds"] >= 0
    assert TEST_TOKEN not in response.text


async def test_protected_endpoints_require_a_bearer_token(anon_client):
    for path in ("/api/tasks", "/api/settings", "/api/security/audit", "/api/memory"):
        response = await anon_client.get(path)
        assert response.status_code == 401, path
        assert response.json()["code"] == "unauthenticated"


async def test_invalid_token_is_rejected(anon_client):
    response = await anon_client.get(
        "/api/tasks", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


async def test_settings_do_not_leak_secrets(client):
    response = await client.get("/api/settings")
    assert response.status_code == 200
    text = response.text
    assert TEST_TOKEN not in text
    assert "owner_token" not in text
    assert "github_token" not in text.lower()
    payload = response.json()
    assert payload["loopback_only"] is True
    assert payload["model_provider"] == "none"
    assert payload["autonomy_level"] == 2


async def test_startup_writes_an_audit_record(client):
    response = await client.get("/api/security/audit", params={"limit": 50})
    assert response.status_code == 200
    events = [event["event_type"] for event in response.json()]
    assert "system.started" in events
    assert "auth.owner_session_created" in events
