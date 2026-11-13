from __future__ import annotations

import pytest
from pydantic import ValidationError

from matrix_ai.core.models import Risk
from matrix_ai.core.policy import PolicyEngine
from matrix_ai.errors import ToolExecutionError
from matrix_ai.integrations.youtube_auth import build_youtube_service, connect_youtube, YOUTUBE_READ_SCOPE
from matrix_ai.tools.base import ToolContext
from matrix_ai.tools.youtube_tools import (
    YouTubeGetChannelTool,
    YouTubeListCommentsTool,
    YouTubeUpdateVideoTool,
    YouTubeUploadVideoInput,
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


class FakeVideos:
    def list(self, **kwargs):
        return FakeRequest({
            "items": [{
                "id": kwargs["id"],
                "snippet": {
                    "channelId": "channel-123",
                    "title": "Old title",
                    "description": "Old description",
                    "categoryId": "22",
                    "tags": ["old"],
                },
                "status": {"privacyStatus": "private", "license": "youtube"},
            }]
        })

    def update(self, **kwargs):
        return FakeRequest({
            "id": kwargs["body"]["id"],
            "snippet": kwargs["body"].get("snippet", {}),
            "status": kwargs["body"].get("status", {}),
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

    def videos(self):
        return FakeVideos()


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
    update = registry.get("youtube.update_video")
    upload = registry.get("youtube.upload_video")
    assert reply.risk is Risk.HIGH
    assert update.risk is Risk.HIGH
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


def test_youtube_oauth_fails_closed_without_a_saved_token(runtime, monkeypatch):
    import matrix_ai.integrations.youtube_auth as auth

    class EmptySecureKeyring:
        @staticmethod
        def get_password(_service, _username):
            return None

    monkeypatch.setattr(auth, "_credential_store", lambda: EmptySecureKeyring())
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


def test_youtube_update_video_preserves_existing_metadata_and_confirms_owner(runtime, monkeypatch):
    import matrix_ai.tools.youtube_tools as youtube_tools

    monkeypatch.setattr(youtube_tools, "build_youtube_service", lambda config, scope: FakeService())
    tool = YouTubeUpdateVideoTool()
    args = tool.validate({"video_id": "abcdefghijk", "title": "New title"})
    output = tool.run_sync(args, ToolContext(
        task_id="test", config=runtime.config, store=runtime.store
    ))
    assert output["video_id"] == "abcdefghijk"
    assert output["title"] == "New title"
    assert tool.verify(args, output).ok


def test_youtube_scheduled_upload_requires_private_visibility_and_timezone():
    with pytest.raises(ValidationError, match="private"):
        YouTubeUploadVideoInput.model_validate({
            "path": "video.mp4",
            "title": "Scheduled video",
            "privacy_status": "public",
            "publish_at": "2030-01-01T12:00:00Z",
        })
    with pytest.raises(Exception, match="timezone"):
        YouTubeUploadVideoInput.model_validate({
            "path": "video.mp4",
            "title": "Scheduled video",
            "publish_at": "2030-01-01T12:00:00",
        })


def test_youtube_upload_defaults_to_private():
    args = YouTubeUploadVideoInput.model_validate({
        "path": "approved/video.mp4",
        "title": "Draft upload",
    })
    assert args.privacy_status == "private"
    assert args.publish_at is None


def test_youtube_credentials_fail_closed_without_secure_keyring(runtime, monkeypatch):
    import matrix_ai.integrations.youtube_auth as auth

    class InsecureKeyring:
        __module__ = "keyrings.alt.file"
        priority = 1

    class FakeKeyringModule:
        @staticmethod
        def get_keyring():
            return InsecureKeyring()

    monkeypatch.setitem(__import__("sys").modules, "keyring", FakeKeyringModule())
    with pytest.raises(ToolExecutionError, match="secure OS credential manager"):
        auth.load_youtube_credentials(runtime.config, YOUTUBE_READ_SCOPE)


def test_youtube_keyring_storage_round_trips_credentials(runtime, monkeypatch):
    import matrix_ai.integrations.youtube_auth as auth

    class SecureKeyring:
        __module__ = "keyring.backends.Windows"
        priority = 1
        values = {}

        @classmethod
        def get_password(cls, service, username):
            return cls.values.get((service, username))

        @classmethod
        def set_password(cls, service, username, value):
            cls.values[(service, username)] = value

    class FakeKeyringModule:
        @staticmethod
        def get_keyring():
            return SecureKeyring()

        get_password = SecureKeyring.get_password
        set_password = SecureKeyring.set_password

    monkeypatch.setitem(__import__("sys").modules, "keyring", FakeKeyringModule())
    class Credentials:
        def to_json(self):
            return '{"refresh_token":"test-only"}'

    auth._persist_credentials(runtime.config, Credentials())
    assert auth.has_youtube_credentials(runtime.config)


def test_public_config_reports_youtube_keyring_state_not_legacy_file(runtime, monkeypatch):
    import matrix_ai.integrations.youtube_auth as auth

    class SecureKeyring:
        __module__ = "keyring.backends.Windows"
        priority = 1
        values = {}

        @classmethod
        def get_password(cls, service, username):
            return cls.values.get((service, username))

        @classmethod
        def set_password(cls, service, username, value):
            cls.values[(service, username)] = value

    class FakeKeyringModule:
        @staticmethod
        def get_keyring():
            return SecureKeyring()

        get_password = SecureKeyring.get_password
        set_password = SecureKeyring.set_password

    monkeypatch.setitem(__import__("sys").modules, "keyring", FakeKeyringModule())
    SecureKeyring.values[(auth._KEYRING_SERVICE, auth._credential_user(runtime.config))] = '{"token":"test"}'
    assert runtime.config.public_dict()["integrations_configured"]["youtube"] is True
