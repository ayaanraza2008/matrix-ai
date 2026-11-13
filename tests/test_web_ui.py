"""Dashboard and asset serving (the UI is part of the product surface)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.acceptance


async def test_dashboard_is_served_with_security_headers(anon_client):
    response = await anon_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "MATRIX" in body
    assert "/assets/app.js" in body
    assert "/assets/styles.css" in body
    # No inline scripts: the CSP allows only same-origin scripts.
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_dashboard_assets_are_served(anon_client):
    script = await anon_client.get("/assets/app.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "function api(" in script.text
    assert "localStorage" not in script.text
    assert "Keep the bearer token only in memory" in script.text

    styles = await anon_client.get("/assets/styles.css")
    assert styles.status_code == 200
    assert "text/css" in styles.headers["content-type"]
    assert "prefers-reduced-motion" in styles.text


async def test_meta_reports_ui_available(anon_client):
    response = await anon_client.get("/api/meta")
    assert response.status_code == 200
    assert response.json()["ui"] is True
    assert response.json()["name"] == "MATRIX"


async def test_api_docs_are_available(anon_client):
    response = await anon_client.get("/api/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "MATRIX"
    assert "/api/tasks" in schema["paths"]
    assert "/api/security/emergency-stop" in schema["paths"]
