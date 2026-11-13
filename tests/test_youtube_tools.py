from __future__ import annotations

import json

import pytest

from matrix_ai.config import MatrixConfig
from matrix_ai.core.models import Risk
from matrix_ai.core.policy import PolicyEngine
from matrix_ai.errors import ConfigError, PermissionDenied, ToolExecutionError
from matrix_ai.tools.registry import build_registry
from matrix_ai.tools.youtube_tools import (
    YouTubeClient,
    YouTubeGetChannelTool,
    YouTubeReplyCommentTool,
    YouTubeUploadVideoTool,
)


def test_youtube_tools_are_registered_and_writes_are_gated(tmp_path):
    config = MatrixConfig(home=tmp_path)
    registry = build_registry(config)

    assert {
        "youtube.get_channel",
        "youtube.list_videos",
        "youtube.list_comments",
        "youtube.reply_to_comment",
        "youtube.upload_video",
    } <= set(registry.names())
    assert registry.get("youtube.get_channel").permission_scope == "youtube.read"
    assert registry.get("youtube.get_channel").risk is Risk.LOW
    assert registry.get("youtube.reply_to_comment").permission_scope == "youtube.write"
    assert registry.get("youtube.reply_to_comment").risk is Risk.HIGH
    assert registry.get("youtube.upload_video").risk is Risk.HIGH


def test_youtube_write_scope_is_opt_in_and_no_wildcards(tmp_path):
    default = MatrixConfig(home=tmp_path / "default")
    engine = PolicyEngine(default)
    assert "youtube.read" in engine.task_scopes(None)
    assert "youtube.write" not in engine.task_scopes(None)

    enabled = MatrixConfig(home=tmp_path / "enabled", additional_scopes=("youtube.write",))
    assert "youtube.write" in PolicyEngine(enabled).task_scopes(None)

    with pytest.raises(ConfigError):
        MatrixConfig(home=tmp_path / "wildcard", additional_scopes=("*",))


def test_youtube_oauth_missing_credentials_fails_without_leaking_secrets(tmp_path):
    client = YouTubeClient(MatrixConfig(home=tmp_path))
    with pytest.raises(ToolExecutionError, match="MATRIX_GOOGLE_CLIENT_ID"):
        client._token()


def test_youtube_channel_tool_returns_minimal_channel_metadata(tmp_path):
    class FakeClient:
        def request(self, method, endpoint, *, params=None, payload=None):
            assert method == "GET"
            assert endpoint == "channels"
            assert params["mine"] == "true"
            return {"items": [{
                "id": "channel-123",
                "snippet": {"title": "Example channel", "description": "about"},
                "contentDetails": {"relatedPlaylists": {"uploads": "uploads-123"}},
                "statistics": {"subscriberCount": "42", "videoCount": "9", "viewCount": "1000"},
            }]}

    tool = YouTubeGetChannelTool(FakeClient())
    output = tool.run_sync(tool.validate({}), None)
    assert output == {
        "channel_id": "channel-123",
        "title": "Example channel",
        "description": "about",
        "uploads_playlist_id": "uploads-123",
        "subscriber_count": "42",
        "video_count": "9",
        "view_count": "1000",
    }
    assert tool.verify(tool.validate({}), output).ok


def test_youtube_reply_is_a_non_idempotent_high_risk_write(tmp_path):
    class FakeClient:
        def __init__(self):
            self.calls = []

        def request(self, method, endpoint, *, params=None, payload=None):
            self.calls.append((method, endpoint, params, payload))
            return {"id": "reply-123"}

    client = FakeClient()
    tool = YouTubeReplyCommentTool(client)
    args = tool.validate({"parent_id": "parent-123", "text": "Thanks for watching!"})
    output = tool.run_sync(args, None)

    assert tool.risk is Risk.HIGH
    assert tool.idempotent is False
    assert output["posted"] is True
    assert client.calls[0][0:2] == ("POST", "comments")
    assert client.calls[0][3]["snippet"]["parentId"] == "parent-123"


def test_youtube_upload_rejects_paths_outside_approved_roots(tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"video")
    config = MatrixConfig(home=tmp_path / "home", file_roots=(approved,))
    tool = YouTubeUploadVideoTool(YouTubeClient(config))
    args = tool.validate({
        "file_path": str(outside),
        "title": "Demo",
        "privacy_status": "private",
    })

    class Context:
        def resolve_path(self, raw, *, must_exist=False):
            from matrix_ai.tools.base import ToolContext
            return ToolContext(
                task_id="test", config=config
            ).resolve_path(raw, must_exist=must_exist)

    with pytest.raises(PermissionDenied):
        tool.run_sync(args, Context())


def test_google_oauth_secrets_are_not_exposed_in_public_config(tmp_path):
    config = MatrixConfig(
        home=tmp_path,
        google_client_id="client-id",
        google_client_secret="client-secret",
        google_refresh_token="refresh-secret",
    )
    public = json.dumps(config.public_dict())
    assert "client-secret" not in public
    assert "refresh-secret" not in public
    assert config.public_dict()["integrations_configured"]["youtube"] is True

def test_youtube_oauth_and_extra_scopes_load_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_HOME", str(tmp_path))
    monkeypatch.setenv("MATRIX_GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("MATRIX_GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MATRIX_GOOGLE_REFRESH_TOKEN", "refresh-secret")
    monkeypatch.setenv("MATRIX_EXTRA_SCOPES", "youtube.write,github.write")

    config = MatrixConfig.from_env()

    assert config.google_client_id == "client-id"
    assert config.google_client_secret == "client-secret"
    assert config.google_refresh_token == "refresh-secret"
    assert set(config.additional_scopes) == {"youtube.write", "github.write"}
