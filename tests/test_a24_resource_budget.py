"""A24 - resource budget is measured and reported on the target PC."""

from __future__ import annotations

import psutil
import pytest

pytestmark = pytest.mark.acceptance


async def test_system_metrics_are_reported(client):
    response = await client.get("/api/system/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["memory"]["total_mb"] > 0
    assert 0 <= payload["memory"]["used_percent"] <= 100
    assert payload["cpu"]["logical_cores"] >= 1
    assert payload["disk"]["total_gb"] > 0
    assert payload["uptime_seconds"] >= 0


async def test_model_budget_and_single_model_rule(client):
    payload = (await client.get("/api/models")).json()
    hardware = payload["hardware"]
    assert hardware["concurrent_models"] == 1
    assert hardware["budget_mb"] == 2500
    assert hardware["reserve_ram_mb"] >= 1024
    assert hardware["total_ram_mb"] > 0
    assert any(profile["ram_mb"] <= hardware["budget_mb"] for profile in hardware["profiles"])


async def test_process_memory_stays_bounded(runtime):
    rss_mb = psutil.Process().memory_info().rss / 1024**2
    assert rss_mb < 1024, f"test process used {rss_mb:.0f} MB, above the 1 GB ceiling"


async def test_database_uses_wal_and_is_healthy(client, runtime):
    journal = runtime.store.db.query_one("PRAGMA journal_mode")[0]
    assert str(journal).lower() == "wal"
    health = (await client.get("/api/health")).json()
    assert health["database"] == "ok"
