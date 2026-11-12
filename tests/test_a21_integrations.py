"""A21 - missing integration credentials never break the core assistant."""

from __future__ import annotations

import pytest
from conftest import execute_tool, wait_for_task

pytestmark = pytest.mark.acceptance


async def test_all_integrations_report_honest_status(client):
    response = await client.get("/api/integrations")
    assert response.status_code == 200
    views = response.json()
    assert len(views) >= 6
    statuses = {view["name"]: view["status"] for view in views}
    assert statuses["weather"] == "configured"
    for name in ("github", "telegram", "google_workspace", "home_assistant", "telephony"):
        assert statuses[name] == "missing_credentials"
        view = next(entry for entry in views if entry["name"] == name)
        assert view["detail"]
        assert view["required_settings"]


async def test_checking_an_unconfigured_integration_does_not_crash(client):
    response = await client.post("/api/integrations/telegram/check")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "missing_credentials"
    assert "MATRIX_TELEGRAM_BOT_TOKEN" in (payload["detail"] or "")

    unknown = await client.post("/api/integrations/not_a_thing/check")
    assert unknown.status_code == 404


async def test_core_task_still_works_without_integrations(client):
    task = await execute_tool(client, "system.get_status", {})
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "COMPLETE"

    health = await client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] in {"ok", "degraded"}
    assert health.json()["integrations_total"] >= 6
