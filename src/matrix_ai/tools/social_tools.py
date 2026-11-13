"""Approval-gated official API actions for LinkedIn, Instagram and WhatsApp.

These are narrow connectors, not browser automation. Provider-side app review,
OAuth scopes, account eligibility, messaging policy and quotas still apply.
"""
from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import AnyHttpUrl, Field, field_validator

from ..core.models import Risk, VerificationEvidence
from ..errors import ToolExecutionError, ToolUnavailable
from .base import StrictModel, Tool, ToolContext

_LINKEDIN_AUTHOR = re.compile(r"^urn:li:(?:person|organization):[0-9]+$")
_LINKEDIN_VERSION = re.compile(r"^[0-9]{6}$")
_GRAPH_VERSION = re.compile(r"^v[0-9]+\.[0-9]+$")
_META_ID = re.compile(r"^[0-9]{5,40}$")


def _response_json(response: httpx.Response, provider: str) -> dict[str, Any]:
    if response.status_code in {401, 403}:
        raise ToolUnavailable(f"{provider} rejected the credentials or required permissions (HTTP {response.status_code}).")
    if response.status_code == 429:
        raise ToolUnavailable(f"{provider} rate limit reached; retry after the provider reset window.")
    if response.status_code >= 500:
        raise ToolUnavailable(f"{provider} is temporarily unavailable (HTTP {response.status_code}).")
    if response.status_code not in {200, 201}:
        raise ToolExecutionError(f"{provider} rejected the request (HTTP {response.status_code}).")
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _request_error(provider: str, exc: httpx.HTTPError) -> ToolExecutionError:
    return ToolExecutionError(f"{provider} request failed ({type(exc).__name__}); credentials and request data were not logged.")


class LinkedInCreatePostInput(StrictModel):
    text: str = Field(min_length=1, max_length=3000)
    visibility: str = Field(default="PUBLIC", pattern="^(PUBLIC|CONNECTIONS)$")


class LinkedInCreatePostTool(Tool):
    name = "linkedin_create_post"
    description = "Publish a text post as the configured LinkedIn member or organization. Requires exact owner approval."
    permission_scope = "linkedin.write"
    risk = Risk.HIGH
    idempotent = False
    timeout_seconds = 20
    input_model = LinkedInCreatePostInput

    def run_sync(self, args: LinkedInCreatePostInput, ctx: ToolContext) -> dict[str, Any]:
        ctx.ensure_active()
        token = ctx.config.linkedin_access_token
        author = ctx.config.linkedin_author_urn
        version = ctx.config.linkedin_api_version
        if not token or not author:
            raise ToolUnavailable("LinkedIn is not configured. Set MATRIX_LINKEDIN_ACCESS_TOKEN and MATRIX_LINKEDIN_AUTHOR_URN.")
        if not _LINKEDIN_AUTHOR.fullmatch(author):
            raise ToolExecutionError("MATRIX_LINKEDIN_AUTHOR_URN must be a person or organization URN.")
        if not _LINKEDIN_VERSION.fullmatch(version):
            raise ToolExecutionError("MATRIX_LINKEDIN_API_VERSION must use YYYYMM format.")
        payload = {
            "author": author,
            "commentary": args.text,
            "visibility": args.visibility,
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        try:
            response = httpx.post(
                "https://api.linkedin.com/rest/posts",
                headers={
                    "Authorization": f"Bearer {token}",
                    "LinkedIn-Version": version,
                    "X-Restli-Protocol-Version": "2.0.0",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=httpx.Timeout(self.timeout_seconds, connect=5.0),
            )
        except httpx.HTTPError as exc:
            raise _request_error("LinkedIn", exc) from exc
        _response_json(response, "LinkedIn")
        post_id = response.headers.get("x-restli-id")
        if not post_id:
            raise ToolExecutionError("LinkedIn accepted the request but did not return a post ID; verify the post before retrying.")
        return {"post_id": post_id, "author": author, "visibility": args.visibility}

    def verify(self, args: LinkedInCreatePostInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("post_id") and output.get("author"))
        return VerificationEvidence(
            ok=ok, confidence=0.98 if ok else 0.0,
            checks=["linkedin_created_post_id_returned"],
        )


class InstagramListCommentsInput(StrictModel):
    media_id: str = Field(min_length=5, max_length=40, pattern=r"^[0-9]+$")
    limit: int = Field(default=20, ge=1, le=50)


class InstagramListCommentsTool(Tool):
    name = "instagram_list_comments"
    description = "Read a bounded page of comments on an Instagram Professional account's media."
    permission_scope = "instagram.read"
    risk = Risk.LOW
    idempotent = True
    returns_untrusted = True
    timeout_seconds = 20
    input_model = InstagramListCommentsInput

    def run_sync(self, args: InstagramListCommentsInput, ctx: ToolContext) -> dict[str, Any]:
        ctx.ensure_active()
        token, version = ctx.config.instagram_access_token, ctx.config.meta_graph_version
        if not token or not version:
            raise ToolUnavailable("Instagram is not configured. Set MATRIX_INSTAGRAM_ACCESS_TOKEN and MATRIX_META_GRAPH_VERSION.")
        if not _GRAPH_VERSION.fullmatch(version):
            raise ToolExecutionError("MATRIX_META_GRAPH_VERSION must use the vNN.N format.")
        try:
            response = httpx.get(
                f"https://graph.facebook.com/{version}/{args.media_id}/comments",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "id,text,username,timestamp", "limit": args.limit},
                timeout=httpx.Timeout(self.timeout_seconds, connect=5.0),
            )
        except httpx.HTTPError as exc:
            raise _request_error("Instagram", exc) from exc
        payload = _response_json(response, "Instagram")
        data = payload.get("data")
        if not isinstance(data, list):
            raise ToolExecutionError("Instagram returned an invalid comments collection.")
        comments = [
            {
                "id": str(item.get("id", "")),
                "text": str(item.get("text", ""))[:1000],
                "username": str(item.get("username", ""))[:100],
                "timestamp": item.get("timestamp"),
            }
            for item in data[: args.limit]
            if isinstance(item, dict) and item.get("id")
        ]
        return {"media_id": args.media_id, "comments": comments, "count": len(comments)}

    def verify(self, args: InstagramListCommentsInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = output.get("media_id") == args.media_id and isinstance(output.get("comments"), list)
        return VerificationEvidence(ok=ok, confidence=0.95 if ok else 0.0, checks=["instagram_comments_listed"])


class InstagramPublishImageInput(StrictModel):
    image_url: AnyHttpUrl
    caption: str = Field(default="", max_length=2200)

    @field_validator("image_url")
    @classmethod
    def _require_https_image(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("Instagram image_url must use HTTPS")
        return value


class InstagramPublishImageTool(Tool):
    name = "instagram_publish_image"
    description = "Publish an image post to the connected Instagram Professional account. Requires critical-action approval."
    permission_scope = "instagram.publish"
    risk = Risk.CRITICAL
    idempotent = False
    timeout_seconds = 45
    input_model = InstagramPublishImageInput

    def run_sync(self, args: InstagramPublishImageInput, ctx: ToolContext) -> dict[str, Any]:
        ctx.ensure_active()
        token = ctx.config.instagram_access_token
        user_id = ctx.config.instagram_user_id
        version = ctx.config.meta_graph_version
        if not token or not user_id or not version:
            raise ToolUnavailable(
                "Instagram publishing is not configured. Set MATRIX_INSTAGRAM_ACCESS_TOKEN, "
                "MATRIX_INSTAGRAM_USER_ID and MATRIX_META_GRAPH_VERSION."
            )
        if not _META_ID.fullmatch(user_id) or not _GRAPH_VERSION.fullmatch(version):
            raise ToolExecutionError("Instagram user ID or Meta Graph API version is invalid.")
        base = f"https://graph.facebook.com/{version}/{user_id}"
        try:
            container_response = httpx.post(
                f"{base}/media",
                headers={"Authorization": f"Bearer {token}"},
                data={"image_url": str(args.image_url), "caption": args.caption},
                timeout=httpx.Timeout(self.timeout_seconds, connect=5.0),
            )
        except httpx.HTTPError as exc:
            raise _request_error("Instagram", exc) from exc
        container = _response_json(container_response, "Instagram")
        creation_id = container.get("id")
        if not creation_id:
            raise ToolExecutionError("Instagram did not confirm the image media container.")
        ctx.ensure_active()
        try:
            publish_response = httpx.post(
                f"{base}/media_publish",
                headers={"Authorization": f"Bearer {token}"},
                data={"creation_id": str(creation_id)},
                timeout=httpx.Timeout(self.timeout_seconds, connect=5.0),
            )
        except httpx.HTTPError as exc:
            raise _request_error("Instagram", exc) from exc
        published = _response_json(publish_response, "Instagram")
        media_id = published.get("id")
        if not media_id:
            raise ToolExecutionError(
                "Instagram created the media container but did not confirm publication; verify the account before retrying."
            )
        return {"media_id": str(media_id), "creation_id": str(creation_id)}

    def verify(self, args: InstagramPublishImageInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("media_id") and output.get("creation_id"))
        return VerificationEvidence(
            ok=ok, confidence=0.98 if ok else 0.0,
            checks=["instagram_media_container_created", "instagram_published_media_id_returned"],
        )


class InstagramReplyCommentInput(StrictModel):
    comment_id: str = Field(min_length=5, max_length=40, pattern=r"^[0-9]+$")
    message: str = Field(min_length=1, max_length=2000)


class InstagramReplyCommentTool(Tool):
    name = "instagram_reply_comment"
    description = "Reply to a comment on the connected Instagram Professional account. Requires exact owner approval."
    permission_scope = "instagram.write"
    risk = Risk.HIGH
    idempotent = False
    timeout_seconds = 20
    input_model = InstagramReplyCommentInput

    def run_sync(self, args: InstagramReplyCommentInput, ctx: ToolContext) -> dict[str, Any]:
        ctx.ensure_active()
        token, version = ctx.config.instagram_access_token, ctx.config.meta_graph_version
        if not token or not version:
            raise ToolUnavailable("Instagram is not configured. Set MATRIX_INSTAGRAM_ACCESS_TOKEN and MATRIX_META_GRAPH_VERSION.")
        if not _GRAPH_VERSION.fullmatch(version):
            raise ToolExecutionError("MATRIX_META_GRAPH_VERSION must use the vNN.N format.")
        try:
            response = httpx.post(
                f"https://graph.facebook.com/{version}/{args.comment_id}/replies",
                headers={"Authorization": f"Bearer {token}"},
                data={"message": args.message},
                timeout=httpx.Timeout(self.timeout_seconds, connect=5.0),
            )
        except httpx.HTTPError as exc:
            raise _request_error("Instagram", exc) from exc
        payload = _response_json(response, "Instagram")
        reply_id = payload.get("id")
        if not reply_id:
            raise ToolExecutionError("Instagram did not confirm the created comment reply.")
        return {"comment_id": args.comment_id, "reply_id": str(reply_id)}

    def verify(self, args: InstagramReplyCommentInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = output.get("comment_id") == args.comment_id and bool(output.get("reply_id"))
        return VerificationEvidence(ok=ok, confidence=0.98 if ok else 0.0, checks=["instagram_reply_id_returned"])


class WhatsAppSendTextInput(StrictModel):
    recipient: str = Field(min_length=8, max_length=16, pattern=r"^\+?[1-9][0-9]{7,14}$")
    text: str = Field(min_length=1, max_length=4096)


class WhatsAppSendTemplateInput(StrictModel):
    recipient: str = Field(min_length=8, max_length=16, pattern=r"^\\+?[1-9][0-9]{7,14}$")
    template_name: str = Field(min_length=1, max_length=512, pattern=r"^[a-zA-Z0-9_]+$")
    language_code: str = Field(default="en_US", pattern=r"^[a-z]{2}_[A-Z]{2}$")
    body_parameters: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("body_parameters")
    @classmethod
    def _bound_template_parameters(cls, values: list[str]) -> list[str]:
        if any(len(value) > 1024 for value in values):
            raise ValueError("template body parameters must be at most 1024 characters")
        return values


class WhatsAppSendTemplateTool(Tool):
    name = "whatsapp_send_template"
    description = "Send an approved WhatsApp Business message template to an opted-in recipient. Requires exact owner approval."
    permission_scope = "whatsapp.write"
    risk = Risk.HIGH
    idempotent = False
    timeout_seconds = 25
    input_model = WhatsAppSendTemplateInput

    def run_sync(self, args: WhatsAppSendTemplateInput, ctx: ToolContext) -> dict[str, Any]:
        ctx.ensure_active()
        token = ctx.config.whatsapp_access_token
        phone_id = ctx.config.whatsapp_phone_number_id
        version = ctx.config.meta_graph_version
        if not token or not phone_id or not version:
            raise ToolUnavailable(
                "WhatsApp Cloud API is not configured. Set MATRIX_WHATSAPP_ACCESS_TOKEN, "
                "MATRIX_WHATSAPP_PHONE_NUMBER_ID and MATRIX_META_GRAPH_VERSION."
            )
        if not _META_ID.fullmatch(phone_id) or not _GRAPH_VERSION.fullmatch(version):
            raise ToolExecutionError("WhatsApp phone-number ID or Meta Graph API version is invalid.")
        template: dict[str, Any] = {
            "name": args.template_name,
            "language": {"code": args.language_code},
        }
        if args.body_parameters:
            template["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": value} for value in args.body_parameters],
            }]
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": args.recipient.lstrip("+"),
            "type": "template",
            "template": template,
        }
        try:
            response = httpx.post(
                f"https://graph.facebook.com/{version}/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=httpx.Timeout(self.timeout_seconds, connect=5.0),
            )
        except httpx.HTTPError as exc:
            raise _request_error("WhatsApp", exc) from exc
        result = _response_json(response, "WhatsApp")
        messages = result.get("messages")
        message_id = messages[0].get("id") if isinstance(messages, list) and messages and isinstance(messages[0], dict) else None
        if not message_id:
            raise ToolExecutionError("WhatsApp did not confirm a template message ID.")
        return {"message_id": str(message_id), "recipient": args.recipient.lstrip("+"), "template": args.template_name}

    def verify(self, args: WhatsAppSendTemplateInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("message_id") and output.get("recipient") == args.recipient.lstrip("+"))
        return VerificationEvidence(ok=ok, confidence=0.98 if ok else 0.0, checks=["whatsapp_template_message_id_returned"])


class WhatsAppSendTextTool(Tool):
    name = "whatsapp_send_text"
    description = "Send a WhatsApp Business Platform text message to an opted-in recipient. Requires exact owner approval."
    permission_scope = "whatsapp.write"
    risk = Risk.HIGH
    idempotent = False
    timeout_seconds = 25
    input_model = WhatsAppSendTextInput

    def run_sync(self, args: WhatsAppSendTextInput, ctx: ToolContext) -> dict[str, Any]:
        ctx.ensure_active()
        token = ctx.config.whatsapp_access_token
        phone_id = ctx.config.whatsapp_phone_number_id
        version = ctx.config.meta_graph_version
        if not token or not phone_id or not version:
            raise ToolUnavailable(
                "WhatsApp Cloud API is not configured. Set MATRIX_WHATSAPP_ACCESS_TOKEN, "
                "MATRIX_WHATSAPP_PHONE_NUMBER_ID and MATRIX_META_GRAPH_VERSION."
            )
        if not _META_ID.fullmatch(phone_id) or not _GRAPH_VERSION.fullmatch(version):
            raise ToolExecutionError("WhatsApp phone-number ID or Meta Graph API version is invalid.")
        recipient = args.recipient.lstrip("+")
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": args.text},
        }
        try:
            response = httpx.post(
                f"https://graph.facebook.com/{version}/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=httpx.Timeout(self.timeout_seconds, connect=5.0),
            )
        except httpx.HTTPError as exc:
            raise _request_error("WhatsApp", exc) from exc
        result = _response_json(response, "WhatsApp")
        messages = result.get("messages")
        message_id = messages[0].get("id") if isinstance(messages, list) and messages and isinstance(messages[0], dict) else None
        if not message_id:
            raise ToolExecutionError("WhatsApp did not confirm a sent message ID.")
        return {"message_id": str(message_id), "recipient": recipient}

    def verify(self, args: WhatsAppSendTextInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("message_id") and output.get("recipient") == args.recipient.lstrip("+"))
        return VerificationEvidence(ok=ok, confidence=0.98 if ok else 0.0, checks=["whatsapp_message_id_returned"])
