from __future__ import annotations

import pytest

from matrix_ai.core.models import Risk
from matrix_ai.core.policy import PolicyEngine
from matrix_ai.errors import ToolExecutionError
from matrix_ai.integrations.youtube_auth import YOUTUBE_READ_SCOPE, build_youtube_service, connect_youtube
from matrix_ai.tools.base import ToolContext
from matrix_ai.tools.youtube_tools import (
    YouTubeGetChannelTool,
    YouTubeListCommentsTool,
)


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeChannels:
    def list(self, **kwargs):
        assert kwargs["mine"] is True
        return FakeRequest({
            "items": [{
                "id": "channel-123",
                "snippet": {"title": "HOWTIVO", "description": "Channel"},
                "statistics": {"videoCount": "12", "viewCount": "340"},
                "contentDetails": {"relatedPlaylists": {"uploads": "uploads-123"}},
            }]
        })


class FakeComments:
    def list(self, **kwargs):
        assert kwargs["videoId"] == "abcdefghijk"
        return FakeRequest({
            "items": [{
                "id": "thread-1",
                "snippet": {
                    "topLevelComment": {
                        "id": "comment-1",
                        "snippet": {
                            "textDisplay": "Nice video",
                            "authorDisplayName": "Viewer",
                            "publishedAt": "2026-01-01T00:00:00Z",
                            "likeCount": 2,
                        },
                    },
                    "totalReplyCount": 0,
                },
            }]
        })


class FakeService:
    def channels(self):
        return FakeChannels()

    def commentThreads(self):
        return FakeComments()


def test_youtube_read_tools_use_authenticated_channel_service(runtime, monkeypatch):
    import matrix_ai.tools.youtube_tools as youtube_tools

    monkeypatch.setattr(youtube_tools, "build_youtube_service", lambda config, scope: FakeService())
    tool = YouTubeGetChannelTool()
    output = tool.run_sync(tool.validate({}), ToolContext(
        task_id="test", config=runtime.config, store=runtime.store
    ))
    assert output["channel_id"] == "channel-123"
    assert output["title"] == "HOWTIVO"
    assert tool.verify({}, output).ok


def test_youtube_comment_reader_returns_bounded_comment_metadata(runtime, monkeypatch):
    import matrix_ai.tools.youtube_tools as youtube_tools

    monkeypatch.setattr(youtube_tools, "build_youtube_service", lambda config, scope: FakeService())
    tool = YouTubeListCommentsTool()
    args = tool.validate({"video_id": "abcdefghijk", "max_results": 10})
    output = tool.run_sync(args, ToolContext(
        task_id="test", config=runtime.config, store=runtime.store
    ))
    assert output["comments"][0]["text"] == "Nice video"
    assert output["comments"][0]["author"] == "Viewer"


def test_youtube_write_tools_are_opt_in_and_approval_gated(runtime):
    registry = runtime.registry
    reply = registry.get("youtube.reply_comment")
    upload = registry.get("youtube.upload_video")
    assert reply.risk is Risk.HIGH
    assert upload.risk is Risk.CRITICAL
    assert "youtube.read" in runtime.policy.task_scopes(None)
    assert "youtube.write" not in runtime.policy.task_scopes(None)
    assert "youtube.upload" not in runtime.policy.task_scopes(None)

    config = runtime.config.model_copy() if hasattr(runtime.config, "model_copy") else runtime.config
    config.additional_scopes = ("youtube.write", "youtube.upload")
    policy = PolicyEngine(config, runtime.store)
    decision = policy.evaluate_call(
        reply.spec(),
        scopes=policy.task_scopes(["youtube.write"]),
        autonomy=2,
    )
    assert decision.allowed is True
    assert decision.requires_approval is True
    upload_decision = policy.evaluate_call(
        upload.spec(),
        scopes=policy.task_scopes(["youtube.upload"]),
        autonomy=2,
    )
    assert upload_decision.allowed is True
    assert upload_decision.requires_approval is True


def test_youtube_oauth_fails_closed_without_a_saved_token(runtime):
    with pytest.raises(ToolExecutionError, match="not connected"):
        build_youtube_service(runtime.config, YOUTUBE_READ_SCOPE)


def test_youtube_auth_requires_owner_supplied_google_client_file(runtime):
    with pytest.raises(ToolExecutionError, match="MATRIX_YOUTUBE_CLIENT_SECRETS"):
        connect_youtube(runtime.config)


@pytest.mark.asyncio
async def test_youtube_integration_is_reported_unconfigured_without_token(runtime):
    integration = runtime.integrations.get("youtube")
    result = await integration.safe_check()
    assert result.status == "missing_credentials"
