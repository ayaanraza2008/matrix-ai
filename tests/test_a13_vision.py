"""A13/A14 - vision capture requires permission and stops cleanly."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.acceptance


async def test_capture_without_permission_is_refused(client):
    response = await client.post("/api/vision/sessions", json={"permission_granted": False})
    assert response.status_code == 403
    assert response.json()["code"] == "camera_permission_denied"

    status = await client.get("/api/vision/status")
    assert status.json()["active_sessions"] == 0


async def test_capture_stops_when_session_stops(client):
    started = await client.post("/api/vision/sessions", json={"permission_granted": True})
    assert started.status_code == 201
    session_id = started.json()["session_id"]
    assert started.json()["state"] == "active"

    first = await client.post(f"/api/vision/sessions/{session_id}/capture")
    assert first.status_code == 200
    assert first.json()["frame"] == 1
    assert first.json()["persisted"] is False

    second = await client.post(f"/api/vision/sessions/{session_id}/capture")
    assert second.json()["frame"] == 2

    stopped = await client.post(f"/api/vision/sessions/{session_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "stopped"

    after = await client.post(f"/api/vision/sessions/{session_id}/capture")
    assert after.status_code == 409
    assert after.json()["code"] == "conflict"

    status = await client.get("/api/vision/status")
    assert status.json()["active_sessions"] == 0


async def test_unknown_session_is_404(client):
    response = await client.post("/api/vision/sessions/nope/capture")
    assert response.status_code == 404


async def test_vision_status_reports_ocr_availability_honestly(client):
    payload = (await client.get("/api/vision/status")).json()
    assert payload["available"] is True
    assert isinstance(payload["ocr_available"], bool)
    if not payload["ocr_available"]:
        assert "Tesseract" in (payload["detail"] or "")
