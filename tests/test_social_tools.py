from __future__ import annotations

import httpx
import pytest

from matrix_ai.config import MatrixConfig
from matrix_ai.core.models import DEFAULT_SCOPES, Risk
from matrix_ai.errors import ToolExecutionError, ToolUnavailable, ValidationFailure
from matrix_ai.tools.base import ToolContext
from matrix_ai.tools.registry import build_registry
from matrix_ai.tools.social_tools import (
    InstagramListCommentsTool,
    InstagramReplyCommentTool,
    LinkedInCreatePostTool,
    WhatsAppSendTextTool,
)


def make_context(tmp_path):
    config = MatrixConfig(
        home=tmp_path,
        linkedin_access_token="linkedin-test-token",
        linkedin_author_urn="urn:li:person:123456",
        linkedin_api_version="202609",
        instagram_access_token="instagram-test-token",
        meta_graph_version="v24.0",
        whatsapp_access_token="whatsapp-test-token",
        whatsapp_phone_number_id="123456789012345",
    )
    return ToolContext(task_id="social-test", config=config)


def response(status=200, payload=None, headers=None, url="https://example.test"):
    return httpx.Response(
        status,
        json=payload if payload is not None else {},
        headers=headers,
        request=httpx.Request("POST", url),
    )


def test_social_tools_are_registered_and_writes_are_approval_gated():
    registry = build_registry()
    linkedin = registry.get("linkedin_create_post")
    instagram_read = registry.get("instagram_list_comments")
    instagram_write = registry.get("instagram_reply_comment")
    whatsapp = registry.get("whatsapp_send_text")

    assert linkedin.permission_scope == "linkedin.write"
    assert linkedin.risk is Risk.HIGH and not linkedin.spec().idempotent
    assert instagram_read.permission_scope == "instagram.read"
    assert instagram_read.risk is Risk.LOW
    assert instagram_write.permission_scope == "instagram.write"
    assert instagram_write.risk is Risk.HIGH and not instagram_write.spec().idempotent
    assert whatsapp.permission_scope == "whatsapp.write"
    assert whatsapp.risk is Risk.HIGH and not whatsapp.spec().idempotent

    assert "linkedin.write" not in DEFAULT_SCOPES
    assert "instagram.read" not in DEFAULT_SCOPES
    assert "instagram.write" not in DEFAULT_SCOPES
    assert "whatsapp.write" not in DEFAULT_SCOPES


def test_linkedin_create_post_uses_current_posts_api_and_requires_provider_id(tmp_path, monkeypatch):
    calls = {}

    def fake_post(url, *, headers, json, timeout):
        calls.update(url=url, headers=headers, json=json)
        return response(
            status=201,
            headers={"x-restli-id": "urn:li:share:98765"},
            url=url,
        )

    monkeypatch.setattr("matrix_ai.tools.social_tools.httpx.post", fake_post)
    tool = LinkedInCreatePostTool()
    args = tool.validate({"text": "MATRIX build update", "visibility": "PUBLIC"})
    result = tool.run_sync(args, make_context(tmp_path))

    assert calls["url"] == "https://api.linkedin.com/rest/posts"
    assert calls["headers"]["LinkedIn-Version"] == "202609"
    assert calls["headers"]["X-Restli-Protocol-Version"] == "2.0.0"
    assert calls["json"]["author"] == "urn:li:person:123456"
    assert calls["json"]["lifecycleState"] == "PUBLISHED"
    assert result["post_id"] == "urn:li:share:98765"
    assert tool.verify(args, result).ok


def test_linkedin_rejects_bad_author_urn(tmp_path):
    ctx = make_context(tmp_path)
    ctx.config.linkedin_author_urn = "https://linkedin.com/in/not-a-urn"
    with pytest.raises(ToolExecutionError, match="AUTHOR_URN"):
        LinkedInCreatePostTool().run_sync(
            LinkedInCreatePostTool().validate({"text": "test"}), ctx
        )


def test_instagram_lists_bounded_comment_metadata(tmp_path, monkeypatch):
    calls = {}

    def fake_get(url, *, headers, params, timeout):
        calls.update(url=url, params=params, headers=headers)
        return response(
            payload={"data": [{"id": "123456789", "text": "Great", "username": "viewer", "timestamp": "2026-11-13T10:00:00Z"}]},
            url=url,
        )

    monkeypatch.setattr("matrix_ai.tools.social_tools.httpx.get", fake_get)
    tool = InstagramListCommentsTool()
    args = tool.validate({"media_id": "987654321", "limit": 5})
    result = tool.run_sync(args, make_context(tmp_path))

    assert calls["url"] == "https://graph.facebook.com/v24.0/987654321/comments"
    assert calls["params"]["limit"] == 5
    assert result["count"] == 1
    assert result["comments"][0]["username"] == "viewer"
    assert tool.verify(args, result).ok


def test_instagram_reply_is_a_write_and_returns_reply_id(tmp_path, monkeypatch):
    calls = {}

    def fake_post(url, *, headers, data, timeout):
        calls.update(url=url, data=data)
        return response(payload={"id": "reply-12345"}, url=url)

    monkeypatch.setattr("matrix_ai.tools.social_tools.httpx.post", fake_post)
    tool = InstagramReplyCommentTool()
    args = tool.validate({"comment_id": "123456789", "message": "Thanks for watching!"})
    result = tool.run_sync(args, make_context(tmp_path))

    assert calls["url"] == "https://graph.facebook.com/v24.0/123456789/replies"
    assert calls["data"] == {"message": "Thanks for watching!"}
    assert result["reply_id"] == "reply-12345"
    assert tool.verify(args, result).ok


def test_whatsapp_sends_text_with_cloud_api_and_provider_message_id(tmp_path, monkeypatch):
    calls = {}

    def fake_post(url, *, headers, json, timeout):
        calls.update(url=url, headers=headers, json=json)
        return response(
            payload={"messages": [{"id": "wamid.test-message"}]},
            url=url,
        )

    monkeypatch.setattr("matrix_ai.tools.social_tools.httpx.post", fake_post)
    tool = WhatsAppSendTextTool()
    args = tool.validate({"recipient": "+919876543210", "text": "Hello from MATRIX"})
    result = tool.run_sync(args, make_context(tmp_path))

    assert calls["url"] == "https://graph.facebook.com/v24.0/123456789012345/messages"
    assert calls["json"]["to"] == "919876543210"
    assert calls["json"]["messaging_product"] == "whatsapp"
    assert result["message_id"] == "wamid.test-message"
    assert tool.verify(args, result).ok


def test_social_tools_fail_safely_on_provider_auth_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "matrix_ai.tools.social_tools.httpx.post",
        lambda url, **kwargs: response(status=403, url=url),
    )
    with pytest.raises(ToolUnavailable, match="credentials or required permissions"):
        LinkedInCreatePostTool().run_sync(
            LinkedInCreatePostTool().validate({"text": "test"}), make_context(tmp_path)
        )


def test_whatsapp_rejects_non_e164_recipient():
    with pytest.raises(ValidationFailure):
        WhatsAppSendTextTool().validate({"recipient": "123", "text": "Hello"})
