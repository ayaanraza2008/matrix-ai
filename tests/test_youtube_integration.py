from __future__ import annotations

import pytest

from matrix_ai.config import MatrixConfig
from matrix_ai.integrations import adapters
from matrix_ai.integrations.adapters import YouTubeIntegration
from matrix_ai.integrations.base import STATUS_DENIED, STATUS_MISSING, STATUS_OK
from matrix_ai.errors import ToolExecutionError


@pytest.mark.asyncio
async def test_youtube_integration_reports_missing_oauth_credentials(tmp_path):
    integration = YouTubeIntegration(MatrixConfig(home=tmp_path))
    result = await integration.safe_check()
    assert result.status == STATUS_MISSING


@pytest.mark.asyncio
async def test_youtube_integration_checks_authenticated_channel(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, _config):
            pass

        def request(self, method, endpoint, *, params=None, payload=None):
            assert method == "GET"
            assert endpoint == "channels"
            return {"items": [{"snippet": {"title": "My channel"}}]}

    monkeypatch.setattr(adapters, "YouTubeClient", FakeClient)
    integration = YouTubeIntegration(MatrixConfig(
        home=tmp_path,
        google_client_id="client",
        google_client_secret="secret",
        google_refresh_token="refresh",
    ))
    result = await integration.check()
    assert result.status == STATUS_OK
    assert "My channel" in result.detail


@pytest.mark.asyncio
async def test_youtube_integration_reports_rejected_oauth(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, _config):
            pass

        def request(self, *args, **kwargs):
            raise ToolExecutionError("YouTube API request failed (HTTP 401).")

    monkeypatch.setattr(adapters, "YouTubeClient", FakeClient)
    integration = YouTubeIntegration(MatrixConfig(
        home=tmp_path,
        google_client_id="client",
        google_client_secret="secret",
        google_refresh_token="refresh",
    ))
    result = await integration.check()
    assert result.status == STATUS_DENIED
