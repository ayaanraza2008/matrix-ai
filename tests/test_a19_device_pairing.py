"""A19 - only paired, non-revoked devices can access private endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.acceptance


async def test_pairing_flow_and_revocation(client, anon_client, app):
    issued = await client.post("/api/auth/pairing-code")
    assert issued.status_code == 200
    code = issued.json()["code"]
    assert len(code) == 6 and code.isdigit()

    paired = await anon_client.post(
        "/api/auth/pair", json={"code": code, "device_name": "Pixel test", "platform": "android"}
    )
    assert paired.status_code == 200
    token = paired.json()["token"]
    device_id = paired.json()["device_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://matrix.test",
        headers={"Authorization": f"Bearer {token}"},
    ) as device_client:
        me = await device_client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["device_id"] == device_id
        assert me.json()["user_id"] == "owner"

        devices = await client.get("/api/devices")
        entries = {device["id"]: device for device in devices.json()}
        assert device_id in entries
        assert entries[device_id]["platform"] == "android"

        revoked = await client.post(f"/api/devices/{device_id}/revoke")
        assert revoked.status_code == 200

        denied = await device_client.get("/api/auth/me")
        assert denied.status_code == 401
        assert denied.json()["code"] == "unauthenticated"


async def test_pairing_code_is_single_use(anon_client, client):
    code = (await client.post("/api/auth/pairing-code")).json()["code"]
    first = await anon_client.post(
        "/api/auth/pair", json={"code": code, "device_name": "One", "platform": "android"}
    )
    assert first.status_code == 200
    second = await anon_client.post(
        "/api/auth/pair", json={"code": code, "device_name": "Two", "platform": "android"}
    )
    assert second.status_code == 401
    assert "already used" in second.json()["message"]


async def test_invalid_pairing_code_is_rejected(anon_client):
    response = await anon_client.post(
        "/api/auth/pair", json={"code": "000000", "device_name": "Evil", "platform": "android"}
    )
    assert response.status_code == 401
