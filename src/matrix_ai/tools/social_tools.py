"""Approval-gated Instagram Graph API and LinkedIn REST API tools."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import AnyHttpUrl, Field, field_validator

from ..core.models import Risk, VerificationEvidence
from ..errors import ToolExecutionError
from ..integrations.social_api import encode_urn, linkedin_request, meta_request
from .base import StrictModel, Tool, ToolContext


class InstagramListMediaInput(StrictModel):
    max_results: int = Field(default=10, ge=1, le=50)


class InstagramListCommentsInput(StrictModel):
    media_id: str = Field(min_length=5, max_length=64, pattern=r"^[0-9]+$")
    max_results: int = Field(default=20, ge=1, le=100)


class InstagramReplyCommentInput(StrictModel):
    comment_id: str = Field(min_length=5, max_length=64, pattern=r"^[0-9]+$")
    text: str = Field(min_length=1, max_length=2000)


class InstagramPublishImageInput(StrictModel):
    image_url: AnyHttpUrl
    caption: str = Field(default="", max_length=2200)

    @field_validator("image_url")
    @classmethod
    def _require_https_image(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("Instagram image_url must use HTTPS")
        return value


class LinkedInListPostsInput(StrictModel):
    max_results: int = Field(default=10, ge=1, le=50)


class LinkedInPostURNInput(StrictModel):
    post_urn: str = Field(min_length=12, max_length=256)

    @field_validator("post_urn")
    @classmethod
    def _validate_urn(cls, value: str) -> str:
        if not value.startswith(("urn:li:activity:", "urn:li:share:", "urn:li:ugcPost:", "urn:li:post:")):
            raise ValueError("post_urn must be a LinkedIn post URN")
        return value


class LinkedInCommentInput(LinkedInPostURNInput):
    text: str = Field(min_length=1, max_length=3000)


class LinkedInCreatePostInput(StrictModel):
    commentary: str = Field(min_length=1, max_length=3000)
    visibility: Literal["PUBLIC", "CONNECTIONS"] = "PUBLIC"


def _meta_profile(config) -> dict[str, Any]:
    if not config.instagram_user_id:
        raise ToolExecutionError("Set MATRIX_INSTAGRAM_USER_ID for the connected Instagram Business/Creator account.")
    payload, _ = meta_request(
        config, "GET", config.instagram_user_id,
        params={"fields": "id,username,account_type,media_count"},
    )
    return payload


def _linkedin_author(config) -> str:
    author = config.linkedin_organization_urn or config.linkedin_person_urn
    if not author or not author.startswith(("urn:li:person:", "urn:li:organization:")):
        raise ToolExecutionError(
            "Set MATRIX_LINKEDIN_PERSON_URN or MATRIX_LINKEDIN_ORGANIZATION_URN to the authorized author."
        )
    return author


class InstagramGetProfileTool(Tool):
    name = "instagram.get_profile"
    description = "Read the connected Instagram Business/Creator profile."
    permission_scope = "instagram.read"
    risk = Risk.LOW
    idempotent = True
    returns_untrusted = True
    timeout_seconds = 30
    input_model = StrictModel

    def run_sync(self, args, ctx: ToolContext) -> dict[str, Any]:
        return _meta_profile(ctx.config)

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("id") and output.get("username"))
        return VerificationEvidence(ok=ok, confidence=0.98 if ok else 0.0, checks=["instagram_profile_read"])


class InstagramListMediaTool(Tool):
    name = "instagram.list_media"
    description = "Read recent media published by the connected Instagram account."
    permission_scope = "instagram.read"
    risk = Risk.LOW
    idempotent = True
    returns_untrusted = True
    timeout_seconds = 45
    input_model = InstagramListMediaInput

    def run_sync(self, args: InstagramListMediaInput, ctx: ToolContext) -> dict[str, Any]:
        if not ctx.config.instagram_user_id:
            raise ToolExecutionError("Set MATRIX_INSTAGRAM_USER_ID for the connected account.")
        payload, _ = meta_request(
            ctx.config, "GET", f"{ctx.config.instagram_user_id}/media",
            params={
                "fields": "id,caption,media_type,media_url,permalink,timestamp,comments_count,like_count",
                "limit": args.max_results,
            },
        )
        return {
            "media": payload.get("data", []),
            "paging": payload.get("paging", {}),
        }

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        ok = isinstance(output.get("media"), list)
        return VerificationEvidence(ok=ok, confidence=0.95, checks=["instagram_media_list_read"])


class InstagramListCommentsTool(Tool):
    name = "instagram.list_comments"
    description = "Read comments on an Instagram media item."
    permission_scope = "instagram.read"
    risk = Risk.LOW
    idempotent = True
    returns_untrusted = True
    timeout_seconds = 45
    input_model = InstagramListCommentsInput

    def run_sync(self, args: InstagramListCommentsInput, ctx: ToolContext) -> dict[str, Any]:
        payload, _ = meta_request(
            ctx.config, "GET", f"{args.media_id}/comments",
            params={"fields": "id,text,username,timestamp,like_count", "limit": args.max_results},
        )
        return {"comments": payload.get("data", []), "paging": payload.get("paging", {})}

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        ok = isinstance(output.get("comments"), list)
        return VerificationEvidence(ok=ok, confidence=0.95, checks=["instagram_comments_read"])


class InstagramReplyCommentTool(Tool):
    name = "instagram.reply_comment"
    description = "Reply to an Instagram comment. Requires instagram.write and exact owner approval."
    permission_scope = "instagram.write"
    risk = Risk.HIGH
    idempotent = False
    timeout_seconds = 45
    input_model = InstagramReplyCommentInput

    def run_sync(self, args: InstagramReplyCommentInput, ctx: ToolContext) -> dict[str, Any]:
        payload, _ = meta_request(
            ctx.config, "POST", f"{args.comment_id}/replies", body={"message": args.text}
        )
        reply_id = payload.get("id")
        if not reply_id:
            raise ToolExecutionError("Meta did not confirm that the Instagram reply was created.")
        return {"reply_id": reply_id, "comment_id": args.comment_id}

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("reply_id"))
        return VerificationEvidence(ok=ok, confidence=0.98 if ok else 0.0, checks=["instagram_reply_confirmed"])


class InstagramPublishImageTool(Tool):
    name = "instagram.publish_image"
    description = "Publish an image post to the connected Instagram account from a public HTTPS image URL. CRITICAL and approval-gated."
    permission_scope = "instagram.write"
    risk = Risk.CRITICAL
    idempotent = False
    timeout_seconds = 90
    input_model = InstagramPublishImageInput

    def run_sync(self, args: InstagramPublishImageInput, ctx: ToolContext) -> dict[str, Any]:
        if not ctx.config.instagram_user_id:
            raise ToolExecutionError("Set MATRIX_INSTAGRAM_USER_ID for the connected account.")
        container, _ = meta_request(
            ctx.config, "POST", f"{ctx.config.instagram_user_id}/media",
            body={"image_url": str(args.image_url), "caption": args.caption},
        )
        creation_id = container.get("id")
        if not creation_id:
            raise ToolExecutionError("Meta did not confirm creation of the Instagram media container.")
        published, _ = meta_request(
            ctx.config, "POST", f"{ctx.config.instagram_user_id}/media_publish",
            body={"creation_id": creation_id},
        )
        media_id = published.get("id")
        if not media_id:
            raise ToolExecutionError("Meta did not confirm that the Instagram image was published.")
        return {"media_id": media_id, "creation_id": creation_id, "published": True}

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("media_id") and output.get("published"))
        return VerificationEvidence(ok=ok, confidence=0.99 if ok else 0.0, checks=["instagram_publish_id_confirmed"])


class LinkedInGetProfileTool(Tool):
    name = "linkedin.get_profile"
    description = "Read the LinkedIn profile identity for the connected access token."
    permission_scope = "linkedin.read"
    risk = Risk.LOW
    idempotent = True
    returns_untrusted = True
    timeout_seconds = 30
    input_model = StrictModel

    def run_sync(self, args, ctx: ToolContext) -> dict[str, Any]:
        payload, _ = linkedin_request(ctx.config, "GET", "/rest/me")
        return {
            "id": payload.get("id"),
            "person_urn": f"urn:li:person:{payload['id']}" if payload.get("id") else None,
            "localized_first_name": payload.get("localizedFirstName"),
            "localized_last_name": payload.get("localizedLastName"),
        }

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("id"))
        return VerificationEvidence(ok=ok, confidence=0.98 if ok else 0.0, checks=["linkedin_profile_read"])


class LinkedInListPostsTool(Tool):
    name = "linkedin.list_posts"
    description = "List recent LinkedIn posts authored by the configured member or organization."
    permission_scope = "linkedin.read"
    risk = Risk.LOW
    idempotent = True
    returns_untrusted = True
    timeout_seconds = 45
    input_model = LinkedInListPostsInput

    def run_sync(self, args: LinkedInListPostsInput, ctx: ToolContext) -> dict[str, Any]:
        payload, _ = linkedin_request(
            ctx.config, "GET", "/rest/posts",
            params={"q": "author", "author": _linkedin_author(ctx.config), "count": args.max_results, "sortBy": "LAST_MODIFIED"},
        )
        elements = payload.get("elements", [])
        return {
            "posts": [
                {
                    "post_urn": item.get("id"),
                    "commentary": item.get("commentary"),
                    "visibility": item.get("visibility"),
                    "lifecycle_state": item.get("lifecycleState"),
                    "created_at": item.get("createdAt"),
                    "last_modified_at": item.get("lastModifiedAt"),
                }
                for item in elements
            ],
            "paging": payload.get("paging", {}),
        }

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        ok = isinstance(output.get("posts"), list)
        return VerificationEvidence(ok=ok, confidence=0.95, checks=["linkedin_posts_read"])


class LinkedInListCommentsTool(Tool):
    name = "linkedin.list_comments"
    description = "Read comments on a LinkedIn post."
    permission_scope = "linkedin.read"
    risk = Risk.LOW
    idempotent = True
    returns_untrusted = True
    timeout_seconds = 45
    input_model = LinkedInPostURNInput

    def run_sync(self, args: LinkedInPostURNInput, ctx: ToolContext) -> dict[str, Any]:
        payload, _ = linkedin_request(
            ctx.config, "GET", f"/rest/socialActions/{encode_urn(args.post_urn)}/comments",
            params={"count": 50},
        )
        return {"comments": payload.get("elements", []), "paging": payload.get("paging", {})}

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        ok = isinstance(output.get("comments"), list)
        return VerificationEvidence(ok=ok, confidence=0.95, checks=["linkedin_comments_read"])


class LinkedInCommentPostTool(Tool):
    name = "linkedin.comment_post"
    description = "Comment on a LinkedIn post. Requires linkedin.write and exact owner approval."
    permission_scope = "linkedin.write"
    risk = Risk.HIGH
    idempotent = False
    timeout_seconds = 45
    input_model = LinkedInCommentInput

    def run_sync(self, args: LinkedInCommentInput, ctx: ToolContext) -> dict[str, Any]:
        author = _linkedin_author(ctx.config)
        payload, headers = linkedin_request(
            ctx.config, "POST", f"/rest/socialActions/{encode_urn(args.post_urn)}/comments",
            body={"actor": author, "message": {"text": args.text}},
        )
        comment_id = payload.get("id") or headers.get("x-restli-id")
        if not comment_id:
            raise ToolExecutionError("LinkedIn did not confirm that the comment was created.")
        return {"comment_id": comment_id, "post_urn": args.post_urn}

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("comment_id"))
        return VerificationEvidence(ok=ok, confidence=0.98 if ok else 0.0, checks=["linkedin_comment_confirmed"])


class LinkedInCreatePostTool(Tool):
    name = "linkedin.create_post"
    description = "Publish a LinkedIn text post as the configured member or organization. CRITICAL and approval-gated."
    permission_scope = "linkedin.write"
    risk = Risk.CRITICAL
    idempotent = False
    timeout_seconds = 60
    input_model = LinkedInCreatePostInput

    def run_sync(self, args: LinkedInCreatePostInput, ctx: ToolContext) -> dict[str, Any]:
        author = _linkedin_author(ctx.config)
        payload, headers = linkedin_request(
            ctx.config, "POST", "/rest/posts",
            body={
                "author": author,
                "commentary": args.commentary,
                "visibility": args.visibility,
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": [],
                },
                "lifecycleState": "PUBLISHED",
            },
        )
        post_urn = payload.get("id") or headers.get("x-restli-id")
        if not post_urn:
            raise ToolExecutionError("LinkedIn did not confirm that the post was published.")
        return {"post_urn": post_urn, "published": True}

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("post_urn") and output.get("published"))
        return VerificationEvidence(ok=ok, confidence=0.99 if ok else 0.0, checks=["linkedin_post_confirmed"])


def build_social_tools() -> list[Tool]:
    return [
        InstagramGetProfileTool(),
        InstagramListMediaTool(),
        InstagramListCommentsTool(),
        InstagramReplyCommentTool(),
        InstagramPublishImageTool(),
        LinkedInGetProfileTool(),
        LinkedInListPostsTool(),
        LinkedInListCommentsTool(),
        LinkedInCommentPostTool(),
        LinkedInCreatePostTool(),
    ]
