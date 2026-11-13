from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest
from matrix_ai.tools.social_tools import (
    InstagramListCommentsTool,
    InstagramPublishImageTool,
    InstagramReplyCommentTool,
    LinkedInCreatePostTool,
    WhatsAppSendTemplateTool,
    WhatsAppSendTextTool,
)

from matrix_ai.config import MatrixConfig
from matrix_ai.core.models import DEFAULT_SCOPES, Risk
from matrix_ai.errors import ToolExecutionError, ToolUnavailable, ValidationFailure
from matrix_ai.tools.base import ToolContext
from matrix_ai.tools.registry import build_registry


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
    instagram_publish = registry.get("instagram_publish_image")
    assert instagram_write.permission_scope == "instagram.write"
    assert instagram_write.risk is Risk.HIGH and not instagram_write.spec().idempotent
    assert instagram_publish.permission_scope == "instagram.publish"
    assert instagram_publish.risk is Risk.CRITICAL and not instagram_publish.spec().idempotent
    assert whatsapp.permission_scope == "whatsapp.write"
    assert whatsapp.risk is Risk.HIGH and not whatsapp.spec().idempotent

    assert "linkedin.write" not in DEFAULT_SCOPES
    assert "instagram.read" not in DEFAULT_SCOPES
    assert "instagram.write" not in DEFAULT_SCOPES
    assert "instagram.publish" not in DEFAULT_SCOPES
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

@pytest.mark.asyncio
async def test_social_integrations_report_missing_and_configured_without_claiming_live_access(runtime):
    for name in ("linkedin", "instagram", "whatsapp"):
        result = await runtime.integrations.get(name).safe_check()
        assert result.status == "missing_credentials"

    runtime.config.linkedin_access_token = "configured-token"
    runtime.config.linkedin_author_urn = "urn:li:person:123"
    runtime.config.instagram_access_token = "configured-token"
    runtime.config.instagram_user_id = "123456789"
    runtime.config.meta_graph_version = "v24.0"
    runtime.config.whatsapp_access_token = "configured-token"
    runtime.config.whatsapp_phone_number_id = "123456789012345"

    for name in ("linkedin", "instagram", "whatsapp"):
        result = await runtime.integrations.get(name).safe_check()
        assert result.status == "configured"
        assert "verified" in (result.detail or "")

def test_instagram_image_publish_uses_container_then_publish_and_requires_confirmation(tmp_path, monkeypatch):
    calls = []

    def fake_post(url, *, headers, data, timeout):
        calls.append((url, data))
        if url.endswith("/media"):
            return response(payload={"id": "container-123"}, url=url)
        return response(payload={"id": "published-456"}, url=url)

    monkeypatch.setattr("matrix_ai.tools.social_tools.httpx.post", fake_post)
    ctx = make_context(tmp_path)
    ctx.config.instagram_user_id = "123456789"
    tool = InstagramPublishImageTool()
    args = tool.validate({
        "image_url": "https://cdn.example.test/matrix.jpg",
        "caption": "MATRIX update",
    })
    result = tool.run_sync(args, ctx)

    assert calls[0][0].endswith("/123456789/media")
    assert calls[0][1]["image_url"] == "https://cdn.example.test/matrix.jpg"
    assert calls[1][0].endswith("/123456789/media_publish")
    assert calls[1][1] == {"creation_id": "container-123"}
    assert result["media_id"] == "published-456"
    assert tool.verify(args, result).ok

def test_instagram_publish_requires_https_image_url():
    with pytest.raises(ValidationFailure):
        InstagramPublishImageTool().validate({"image_url": "http://example.com/photo.jpg"})

@pytest.mark.asyncio
async def test_whatsapp_webhook_verifies_challenge_and_hmac_signature(client, runtime):
    runtime.config.whatsapp_verify_token = "test-verify-token"
    runtime.config.whatsapp_app_secret = "test-app-secret"
    runtime.config.whatsapp_allowed_senders = ("919876543210",)

    handshake = await client.get(
        "/api/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify-token",
            "hub.challenge": "challenge-123",
        },
    )
    assert handshake.status_code == 200
    assert handshake.text == "challenge-123"

    raw = (
        b'{"object":"whatsapp_business_account","entry":[{"changes":[{"field":"messages",'
        b'"value":{"messages":[{"from":"919876543210","id":"wamid.test-1","type":"text",'
        b'"text":{"body":"Check my task status"}}]}}]}]}'
    )
    signature = "sha256=" + hmac.new(b"test-app-secret", raw, hashlib.sha256).hexdigest()
    received = await client.post(
        "/api/webhooks/whatsapp",
        content=raw,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )
    assert received.status_code == 200, received.text
    assert received.json()["accepted"] == 1
    stored = runtime.store.db.query(
        "SELECT original_request FROM tasks WHERE kind='chat' AND original_request LIKE 'Owner-authorized WhatsApp message%'"
    )
    assert len(stored) == 1
    assert "Check my task status" in stored[0]["original_request"]

    duplicate = await client.post(
        "/api/webhooks/whatsapp",
        content=raw,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicates"] == 1

@pytest.mark.asyncio
async def test_whatsapp_webhook_rejects_bad_signature_and_unallowlisted_sender(client, runtime):
    runtime.config.whatsapp_verify_token = "test-verify-token"
    runtime.config.whatsapp_app_secret = "test-app-secret"
    runtime.config.whatsapp_allowed_senders = ("919876543210",)
    raw = b'{"object":"whatsapp_business_account","entry":[]}'
    bad = await client.post(
        "/api/webhooks/whatsapp",
        content=raw,
        headers={"X-Hub-Signature-256": "sha256=invalid"},
    )
    assert bad.status_code == 401

    other = (
        b'{"object":"whatsapp_business_account","entry":[{"changes":[{"field":"messages",'
        b'"value":{"messages":[{"from":"14155552671","id":"wamid.other","type":"text",'
        b'"text":{"body":"do something"}}]}}]}]}'
    )
    signature = "sha256=" + hmac.new(b"test-app-secret", other, hashlib.sha256).hexdigest()
    ignored = await client.post(
        "/api/webhooks/whatsapp",
        content=other,
        headers={"X-Hub-Signature-256": signature},
    )
    assert ignored.status_code == 200
    assert ignored.json()["accepted"] == 0
    assert ignored.json()["ignored"] == 1

def test_whatsapp_sends_approved_template_with_bounded_body_parameters(tmp_path, monkeypatch):
    calls = {}

    def fake_post(url, *, headers, json, timeout):
        calls.update(url=url, json=json)
        return response(payload={"messages": [{"id": "wamid.template-123"}]}, url=url)

    monkeypatch.setattr("matrix_ai.tools.social_tools.httpx.post", fake_post)
    tool = WhatsAppSendTemplateTool()
    args = tool.validate({
        "recipient": "+919876543210",
        "template_name": "order_update",
        "language_code": "en_US",
        "body_parameters": ["A-123", "shipped"],
    })
    result = tool.run_sync(args, make_context(tmp_path))

    assert calls["json"]["type"] == "template"
    assert calls["json"]["template"]["name"] == "order_update"
    assert calls["json"]["template"]["components"][0]["parameters"][0]["text"] == "A-123"
    assert result["message_id"] == "wamid.template-123"
    assert tool.verify(args, result).ok

def test_whatsapp_template_rejects_unbounded_parameters():
    with pytest.raises(ValidationFailure):
        WhatsAppSendTemplateTool().validate({
            "recipient": "+919876543210",
            "template_name": "order_update",
            "body_parameters": ["x" * 1025],
        })
