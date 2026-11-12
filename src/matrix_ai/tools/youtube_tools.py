"""Official YouTube Data API tools.

Read actions are available with the default youtube.read scope. Public writes
(replying to comments) and uploads require separate owner opt-in scopes and
still pass through MATRIX's per-action approval policy.
"""
from __future__ import annotations

import mimetypes
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from ..core.models import Risk, VerificationEvidence
from ..errors import ToolExecutionError
from ..integrations.youtube_auth import (
    YOUTUBE_READ_SCOPE,
    YOUTUBE_UPLOAD_SCOPE,
    YOUTUBE_WRITE_SCOPE,
    build_youtube_service,
)
from .base import StrictModel, Tool, ToolContext


class YouTubeListVideosInput(StrictModel):
    max_results: int = Field(default=10, ge=1, le=50)
    page_token: str | None = Field(default=None, max_length=500)


class YouTubeListCommentsInput(StrictModel):
    video_id: str = Field(min_length=6, max_length=128)
    max_results: int = Field(default=20, ge=1, le=100)
    page_token: str | None = Field(default=None, max_length=500)


class YouTubeReplyCommentInput(StrictModel):
    parent_comment_id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=10000)


class YouTubeUploadVideoInput(StrictModel):
    path: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=500)
    category_id: str = Field(default="22", pattern=r"^[0-9]{1,3}$")
    privacy_status: Literal["private", "unlisted", "public"] = "private"
    publish_at: str | None = Field(default=None, max_length=40)
    made_for_kids: bool = False

    @model_validator(mode="after")
    def _validate_scheduled_publish(self):
        if self.publish_at:
            if self.privacy_status != "private":
                raise ValueError("scheduled publishing requires private privacy_status")
            try:
                scheduled = datetime.fromisoformat(self.publish_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("publish_at must be an ISO 8601 timestamp") from exc
            if scheduled.tzinfo is None:
                raise ValueError("publish_at must include a timezone")
            if scheduled <= datetime.now(UTC):
                raise ValueError("publish_at must be in the future")
        return self


class YouTubeUpdateVideoInput(StrictModel):
    video_id: str = Field(min_length=6, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = Field(default=None, max_length=500)
    privacy_status: Literal["private", "unlisted", "public"] | None = None

    @model_validator(mode="after")
    def _require_update(self):
        if all(value is None for value in (self.title, self.description, self.tags, self.privacy_status)):
            raise ValueError("at least one video field must be updated")
        return self


def _execute(request):
    try:
        return request.execute()
    except Exception as exc:  # noqa: BLE001 - Google client exceptions can contain user data
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 401:
            raise ToolExecutionError("YouTube authorization expired or was revoked; reconnect the account.") from exc
        if status == 403:
            raise ToolExecutionError("YouTube denied this action. Check API quota, channel permissions and OAuth scopes.") from exc
        if status == 404:
            raise ToolExecutionError("The requested YouTube resource was not found or is not accessible.") from exc
        raise ToolExecutionError("The YouTube API request failed; inspect integration health and try again.") from exc


class YouTubeGetChannelTool(Tool):
    name = "youtube.get_channel"
    description = "Read the connected YouTube channel identity, statistics and uploads playlist."
    permission_scope = "youtube.read"
    risk = Risk.LOW
    idempotent = True
    returns_untrusted = True
    timeout_seconds = 30
    input_model = StrictModel

    def run_sync(self, args, ctx: ToolContext) -> dict[str, Any]:
        service = build_youtube_service(ctx.config, YOUTUBE_READ_SCOPE)
        response = _execute(service.channels().list(
            part="snippet,statistics,contentDetails",
            mine=True,
            maxResults=1,
        ))
        items = response.get("items", [])
        if not items:
            raise ToolExecutionError("The connected Google account has no accessible YouTube channel.")
        channel = items[0]
        snippet = channel.get("snippet", {})
        statistics = channel.get("statistics", {})
        content = channel.get("contentDetails", {}).get("relatedPlaylists", {})
        return {
            "channel_id": channel.get("id"),
            "title": snippet.get("title"),
            "description": snippet.get("description", "")[:1000],
            "custom_url": snippet.get("customUrl"),
            "published_at": snippet.get("publishedAt"),
            "subscriber_count": statistics.get("subscriberCount"),
            "video_count": statistics.get("videoCount"),
            "view_count": statistics.get("viewCount"),
            "uploads_playlist_id": content.get("uploads"),
        }

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("channel_id"))
        return VerificationEvidence(
            ok=ok,
            confidence=0.98 if ok else 0.0,
            checks=["authenticated_channel_returned" if ok else "channel_missing"],
        )


class YouTubeListVideosTool(Tool):
    name = "youtube.list_videos"
    description = "List recent uploads from the connected YouTube channel."
    permission_scope = "youtube.read"
    risk = Risk.LOW
    idempotent = True
    returns_untrusted = True
    timeout_seconds = 45
    input_model = YouTubeListVideosInput

    def run_sync(self, args: YouTubeListVideosInput, ctx: ToolContext) -> dict[str, Any]:
        service = build_youtube_service(ctx.config, YOUTUBE_READ_SCOPE)
        channel = _execute(service.channels().list(part="contentDetails", mine=True, maxResults=1))
        items = channel.get("items", [])
        if not items:
            raise ToolExecutionError("The connected Google account has no accessible YouTube channel.")
        uploads_id = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not uploads_id:
            raise ToolExecutionError("YouTube did not return the channel's uploads playlist.")
        request = service.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_id,
            maxResults=args.max_results,
            pageToken=args.page_token,
        )
        response = _execute(request)
        videos = []
        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            content = item.get("contentDetails", {})
            videos.append({
                "video_id": content.get("videoId"),
                "title": snippet.get("title"),
                "description": snippet.get("description", "")[:1000],
                "published_at": content.get("videoPublishedAt") or snippet.get("publishedAt"),
                "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url"),
            })
        return {"videos": videos, "next_page_token": response.get("nextPageToken")}

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        return VerificationEvidence(
            ok=isinstance(output.get("videos"), list),
            confidence=0.95,
            checks=["uploads_playlist_read"],
        )


class YouTubeListCommentsTool(Tool):
    name = "youtube.list_comments"
    description = "Read top-level comments and replies for a video owned by the connected channel."
    permission_scope = "youtube.read"
    risk = Risk.LOW
    idempotent = True
    returns_untrusted = True
    timeout_seconds = 45
    input_model = YouTubeListCommentsInput

    def run_sync(self, args: YouTubeListCommentsInput, ctx: ToolContext) -> dict[str, Any]:
        service = build_youtube_service(ctx.config, YOUTUBE_READ_SCOPE)
        response = _execute(service.commentThreads().list(
            part="snippet,replies",
            videoId=args.video_id,
            maxResults=args.max_results,
            pageToken=args.page_token,
            order="time",
        ))
        comments = []
        for item in response.get("items", []):
            snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            comments.append({
                "comment_id": item.get("snippet", {}).get("topLevelComment", {}).get("id"),
                "video_id": args.video_id,
                "text": (snippet.get("textOriginal") or snippet.get("textDisplay", ""))[:5000],
                "author": snippet.get("authorDisplayName"),
                "published_at": snippet.get("publishedAt"),
                "like_count": snippet.get("likeCount", 0),
                "reply_count": item.get("snippet", {}).get("totalReplyCount", 0),
            })
        return {"comments": comments, "next_page_token": response.get("nextPageToken")}

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        return VerificationEvidence(
            ok=isinstance(output.get("comments"), list),
            confidence=0.95,
            checks=["video_comment_threads_read"],
        )


class YouTubeReplyCommentTool(Tool):
    name = "youtube.reply_comment"
    description = "Post a reply to a YouTube comment. Requires youtube.write opt-in and exact owner approval."
    permission_scope = "youtube.write"
    risk = Risk.HIGH
    idempotent = False
    timeout_seconds = 60
    input_model = YouTubeReplyCommentInput

    def run_sync(self, args: YouTubeReplyCommentInput, ctx: ToolContext) -> dict[str, Any]:
        service = build_youtube_service(ctx.config, YOUTUBE_WRITE_SCOPE)
        response = _execute(service.comments().insert(
            part="snippet",
            body={"snippet": {"parentId": args.parent_comment_id, "textOriginal": args.text}},
        ))
        snippet = response.get("snippet", {})
        comment_id = response.get("id")
        if not comment_id:
            raise ToolExecutionError("YouTube did not confirm that the reply was created.")
        return {
            "comment_id": comment_id,
            "parent_comment_id": snippet.get("parentId"),
            "text": snippet.get("textOriginal", args.text),
            "published_at": snippet.get("publishedAt"),
        }

    def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("comment_id"))
        return VerificationEvidence(
            ok=ok,
            confidence=0.99 if ok else 0.0,
            checks=["provider_returned_reply_id" if ok else "reply_not_confirmed"],
        )


class YouTubeUpdateVideoTool(Tool):
    name = "youtube.update_video"
    description = "Update metadata or privacy for a video owned by the connected channel. Requires youtube.write and exact owner approval."
    permission_scope = "youtube.write"
    risk = Risk.HIGH
    idempotent = False
    timeout_seconds = 60
    input_model = YouTubeUpdateVideoInput

    def run_sync(self, args: YouTubeUpdateVideoInput, ctx: ToolContext) -> dict[str, Any]:
        service = build_youtube_service(ctx.config, YOUTUBE_WRITE_SCOPE)
        channel = _execute(service.channels().list(part="id", mine=True, maxResults=1))
        channel_items = channel.get("items", [])
        if not channel_items:
            raise ToolExecutionError("The connected Google account has no accessible YouTube channel.")
        channel_id = channel_items[0].get("id")
        existing = _execute(service.videos().list(
            part="snippet,status", id=args.video_id, maxResults=1
        )).get("items", [])
        if not existing:
            raise ToolExecutionError("The requested YouTube video was not found.")
        current = existing[0]
        snippet = current.get("snippet", {})
        if snippet.get("channelId") != channel_id:
            raise ToolExecutionError("The requested video does not belong to the connected channel.")
        parts = []
        body: dict[str, Any] = {"id": args.video_id}
        if args.title is not None or args.description is not None or args.tags is not None:
            body["snippet"] = {
                "title": args.title if args.title is not None else snippet.get("title", ""),
                "description": args.description if args.description is not None else snippet.get("description", ""),
                "categoryId": snippet.get("categoryId", "22"),
                "tags": args.tags if args.tags is not None else snippet.get("tags", []),
            }
            parts.append("snippet")
        if args.privacy_status is not None:
            status = current.get("status", {})
            body["status"] = {
                "privacyStatus": args.privacy_status,
                "license": status.get("license", "youtube"),
                "embeddable": status.get("embeddable", True),
                "publicStatsViewable": status.get("publicStatsViewable", True),
                "selfDeclaredMadeForKids": status.get("selfDeclaredMadeForKids", False),
            }
            parts.append("status")
        updated = _execute(service.videos().update(part=",".join(parts), body=body))
        return {
            "video_id": updated.get("id"),
            "title": updated.get("snippet", {}).get("title"),
            "privacy_status": updated.get("status", {}).get("privacyStatus"),
        }

    def verify(self, args: YouTubeUpdateVideoInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = output.get("video_id") == args.video_id
        return VerificationEvidence(
            ok=ok,
            confidence=0.98 if ok else 0.0,
            checks=["owned_video_update_confirmed" if ok else "video_update_not_confirmed"],
        )


class YouTubeUploadVideoTool(Tool):
    name = "youtube.upload_video"
    description = "Upload a video file to the connected YouTube channel. Requires youtube.upload opt-in and exact owner approval."
    permission_scope = "youtube.upload"
    risk = Risk.CRITICAL
    idempotent = False
    timeout_seconds = 1800
    max_output_bytes = 4000
    input_model = YouTubeUploadVideoInput

    def run_sync(self, args: YouTubeUploadVideoInput, ctx: ToolContext) -> dict[str, Any]:
        if args.publish_at:
            scheduled = datetime.fromisoformat(args.publish_at.replace("Z", "+00:00"))
            if scheduled.tzinfo is None or scheduled <= datetime.now(UTC):
                raise ToolExecutionError("Scheduled publish time must remain in the future and include a timezone.")
        path = ctx.resolve_path(args.path, must_exist=True)
        if not path.is_file():
            raise ToolExecutionError("The upload source must be a regular file.")
        max_bytes = int(getattr(ctx.config, "youtube_max_upload_bytes", 2_000_000_000))
        size = path.stat().st_size
        if size <= 0 or size > max_bytes:
            raise ToolExecutionError(f"Video size must be between 1 byte and {max_bytes} bytes.")
        service = build_youtube_service(ctx.config, YOUTUBE_UPLOAD_SCOPE)
        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            raise ToolExecutionError("Install MATRIX's optional YouTube dependencies: pip install -e '.[youtube]'.") from exc
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        media = MediaFileUpload(
            str(path),
            mimetype=mime_type,
            chunksize=8 * 1024 * 1024,
            resumable=True,
        )
        body = {
            "snippet": {
                "title": args.title,
                "description": args.description,
                "tags": args.tags,
                "categoryId": args.category_id,
            },
            "status": {
                "privacyStatus": args.privacy_status,
                "selfDeclaredMadeForKids": args.made_for_kids,
                **({"publishAt": args.publish_at} if args.publish_at else {}),
            },
        }
        response = _execute(service.videos().insert(part="snippet,status", body=body, media_body=media))
        video_id = response.get("id")
        if not video_id:
            raise ToolExecutionError("YouTube did not confirm the uploaded video ID.")
        return {
            "video_id": video_id,
            "title": response.get("snippet", {}).get("title", args.title),
            "privacy_status": response.get("status", {}).get("privacyStatus", args.privacy_status),
            "upload_status": response.get("status", {}).get("uploadStatus"),
            "publish_at": response.get("status", {}).get("publishAt"),
            "url": f"https://www.youtube.com/watch?v={video_id}",
        }

    def verify(self, args: YouTubeUploadVideoInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("video_id"))
        return VerificationEvidence(
            ok=ok,
            confidence=0.99 if ok else 0.0,
            checks=["provider_returned_video_id" if ok else "upload_not_confirmed"],
        )


def build_youtube_tools() -> list[Tool]:
    return [
        YouTubeGetChannelTool(),
        YouTubeListVideosTool(),
        YouTubeListCommentsTool(),
        YouTubeReplyCommentTool(),
        YouTubeUpdateVideoTool(),
        YouTubeUploadVideoTool(),
    ]
