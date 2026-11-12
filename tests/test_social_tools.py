from __future__ import annotations

import pytest

from matrix_ai.core.models import Risk
from matrix_ai.core.policy import PolicyEngine
from matrix_ai.integrations.adapters import InstagramIntegration, LinkedInIntegration
from matrix_ai.tools.base import ToolContext


def _ctx(runtime):
    return ToolContext(task_id="social-test", config=runtime.config, store=runtime.store)


def test_instagram_profile_and_media_reads_are_untrusted(runtime, monkeypatch):
    import matrix_ai.tools.social_tools as social

    calls = []

    def fake_meta(config, method, endpoint, *, params=None, body=None):
        calls.append((method, endpoint, params, body))
        if endpoint == config.instagram_user_id:
            return {"id": "17840001", "username": "howtivo", "account_type": "BUSINESS"}, {}
        return {"data": [{"id": "media-1", "caption": "new upload"}]}, {}

    runtime.config.instagram_user_id = "17840001"
    monkeypatch.setattr(social, "meta_request", fake_meta)

    profile = runtime.registry.get("instagram.get_profile")
    profile_output = profile.run_sync(profile.validate({}), _ctx(runtime))
    assert profile_output["username"] == "howtivo"
    assert profile.returns_untrusted is True

    media = runtime.registry.get("instagram.list_media")
    media_output = media.run_sync(media.validate({"max_results": 5}), _ctx(runtime))
    assert media_output["media"][0]["id"] == "media-1"
    assert calls[1][1] == "17840001/media"


def test_instagram_reply_and_image_publish_are_approval_gated(runtime, monkeypatch):
    import matrix_ai.tools.social_tools as social

    calls = []

    def fake_meta(config, method, endpoint, *, params=None, body=None):
        calls.append((method, endpoint, body))
        if endpoint.endswith("/media"):
            return {"id": "container-1"}, {}
        if endpoint.endswith("/media_publish"):
            return {"id": "published-1"}, {}
        return {"id": "reply-1"}, {}

    runtime.config.instagram_user_id = "17840001"
    monkeypatch.setattr(social, "meta_request", fake_meta)

    reply = runtime.registry.get("instagram.reply_comment")
    reply_output = reply.run_sync(
        reply.validate({"comment_id": "123456", "text": "Thanks for watching!"}), _ctx(runtime)
    )
    assert reply_output["reply_id"] == "reply-1"

    publish = runtime.registry.get("instagram.publish_image")
    publish_output = publish.run_sync(
        publish.validate({"image_url": "https://cdn.example.com/image.jpg", "caption": "Hello"}),
        _ctx(runtime),
    )
    assert publish_output["media_id"] == "published-1"
    assert [call[1] for call in calls[-2:]] == ["17840001/media", "17840001/media_publish"]


def test_linkedin_profile_posts_and_member_urn_resolution(runtime, monkeypatch):
    import matrix_ai.tools.social_tools as social

    def fake_linkedin(config, method, endpoint, *, params=None, body=None):
        if endpoint == "/rest/me":
            return {"id": "member-123", "localizedFirstName": "Ayaan"}, {}
        if endpoint == "/rest/posts":
            assert params["author"] == "urn:li:person:member-123"
            return {"elements": [{"id": "urn:li:ugcPost:post-1", "commentary": "Hello"}]}, {}
        return {"elements": []}, {}

    runtime.config.linkedin_person_urn = None
    runtime.config.linkedin_organization_urn = None
    monkeypatch.setattr(social, "linkedin_request", fake_linkedin)

    profile = runtime.registry.get("linkedin.get_profile")
    result = profile.run_sync(profile.validate({}), _ctx(runtime))
    assert result["person_urn"] == "urn:li:person:member-123"

    posts = runtime.registry.get("linkedin.list_posts")
    output = posts.run_sync(posts.validate({"max_results": 5}), _ctx(runtime))
    assert output["posts"][0]["post_urn"] == "urn:li:ugcPost:post-1"


def test_linkedin_post_and_comment_writes_are_approval_gated(runtime, monkeypatch):
    import matrix_ai.tools.social_tools as social

    def fake_linkedin(config, method, endpoint, *, params=None, body=None):
        if endpoint == "/rest/me":
            return {"id": "member-123"}, {}
        return {}, {"x-restli-id": "urn:li:ugcPost:created-1"}

    monkeypatch.setattr(social, "linkedin_request", fake_linkedin)
    create = runtime.registry.get("linkedin.create_post")
    output = create.run_sync(
        create.validate({"commentary": "A new update from MATRIX."}), _ctx(runtime)
    )
    assert output["post_urn"] == "urn:li:ugcPost:created-1"

    comment = runtime.registry.get("linkedin.comment_post")
    commented = comment.run_sync(
        comment.validate({"post_urn": "urn:li:ugcPost:post-1", "text": "Great work!"}),
        _ctx(runtime),
    )
    assert commented["comment_id"] == "urn:li:ugcPost:created-1"


def test_social_write_scopes_are_opt_in_and_high_impact(runtime):
    assert "instagram.read" in runtime.policy.task_scopes(None)
    assert "linkedin.read" in runtime.policy.task_scopes(None)
    assert "instagram.write" not in runtime.policy.task_scopes(None)
    assert "linkedin.write" not in runtime.policy.task_scopes(None)

    for name, risk in [
        ("instagram.reply_comment", Risk.HIGH),
        ("instagram.publish_image", Risk.CRITICAL),
        ("linkedin.comment_post", Risk.HIGH),
        ("linkedin.create_post", Risk.CRITICAL),
    ]:
        tool = runtime.registry.get(name)
        assert tool.risk is risk
        assert tool.returns_untrusted is False

    runtime.config.additional_scopes = ("instagram.write", "linkedin.write")
    policy = PolicyEngine(runtime.config, runtime.store)
    for name, scope in [
        ("instagram.reply_comment", "instagram.write"),
        ("linkedin.create_post", "linkedin.write"),
    ]:
        decision = policy.evaluate_call(
            runtime.registry.get(name).spec(),
            scopes=policy.task_scopes([scope]),
            autonomy=2,
        )
        assert decision.allowed is True
        assert decision.requires_approval is True


@pytest.mark.asyncio
async def test_social_integrations_are_unconfigured_without_owner_tokens(runtime):
    instagram = await InstagramIntegration(runtime.config).safe_check()
    linkedin = await LinkedInIntegration(runtime.config).safe_check()
    assert instagram.status == "missing_credentials"
    assert linkedin.status == "missing_credentials"
