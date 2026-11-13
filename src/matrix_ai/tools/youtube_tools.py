"""Official YouTube Data API v3 tools with OAuth refresh and approval-gated writes.

The connector is opt-in, uses Google OAuth refresh-token credentials from the
environment, and never logs or returns access/refresh tokens. Public mutations
are HIGH risk and remain subject to MATRIX's owner approval flow.
"""
from __future__ import annotations

import mimetypes
import threading
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import Field

from ..config import MatrixConfig
from ..core.models import Risk, VerificationEvidence
from ..errors import ToolExecutionError
from .base import StrictModel, Tool, ToolContext

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
MAX_VIDEO_BYTES = 512 * 1024 * 1024


class YouTubeClient:
    """Small synchronous API client; calls run inside MATRIX's worker thread."""

    def __init__(self, config: MatrixConfig) -> None:
        self.config = config
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._token_lock = threading.Lock()

    def _token(self) -> str:
        with self._token_lock:
            return self._get_token()

    def _get_token(self) -> str:
        if self._access_token and time.monotonic() < self._expires_at - 60:
            return self._access_token
        if not all((
            self.config.google_client_id,
            self.config.google_client_secret,
            self.config.google_refresh_token,
        )):
            raise ToolExecutionError(
                "YouTube OAuth is not configured. Set MATRIX_GOOGLE_CLIENT_ID, "
                "MATRIX_GOOGLE_CLIENT_SECRET and MATRIX_GOOGLE_REFRESH_TOKEN."
            )
        try:
            with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                response = client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "client_id": self.config.google_client_id,
                        "client_secret": self.config.google_client_secret,
                        "refresh_token": self.config.google_refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
                response.raise_for_status()
                payload = response.json()
            token = payload.get("access_token")
            expires_in = payload.get("expires_in", 3600)
            if not isinstance(token, str) or not token or not isinstance(expires_in, (int, float)):
                raise ToolExecutionError("Google OAuth returned an invalid access-token response.")
            self._access_token = token
            self._expires_at = time.monotonic() + max(60.0, float(expires_in))
            return token
        except ToolExecutionError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            self._access_token = None
            raise ToolExecutionError(
                "Google OAuth token refresh failed. Check the OAuth client, refresh token and granted scopes."
            ) from exc

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = self._token()
        try:
            with httpx.Client(timeout=httpx.Timeout(30.0, connect=8.0)) as client:
                response = client.request(
                    method,
                    f"{YOUTUBE_API}/{endpoint.lstrip('/')}",
                    params=params,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
                if response.status_code == 401:
                    with self._token_lock:
                        self._access_token = None
                        self._expires_at = 0.0
                    token = self._token()
                    response = client.request(
                        method,
                        f"{YOUTUBE_API}/{endpoint.lstrip('/')}",
                        params=params,
                        json=payload,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                response.raise_for_status()
                result = response.json()
            if not isinstance(result, dict):
                raise ToolExecutionError("YouTube API returned an invalid response.")
            return result
        except ToolExecutionError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" (HTTP {status})" if status else ""
            raise ToolExecutionError(
                f"YouTube API request failed{suffix}. Check OAuth scopes, quota and channel permissions."
            ) from exc

    def upload(self, path: Path, metadata: dict[str, Any]) -> dict[str, Any]:
        token = self._token()
        size = path.stat().st_size
        mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
        body = {
            "snippet": {
                "title": metadata["title"],
                "description": metadata["description"],
                "tags": metadata["tags"],
                "categoryId": metadata["category_id"],
            },
            "status": {"privacyStatus": metadata["privacy_status"]},
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
                start = client.post(
                    "https://www.googleapis.com/upload/youtube/v3/videos",
                    params={"uploadType": "resumable", "part": "snippet,status"},
                    json=body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Upload-Content-Type": mime,
                        "X-Upload-Content-Length": str(size),
                    },
                )
                start.raise_for_status()
                upload_url = start.headers.get("Location")
                if not upload_url or not upload_url.startswith(
                    "https://www.googleapis.com/upload/youtube/v3/videos?"
                ):
                    raise ToolExecutionError("YouTube did not return a valid resumable-upload URL.")
                with path.open("rb") as video_file:
                    uploaded = client.put(
                        upload_url,
                        content=video_file,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": mime,
                            "Content-Length": str(size),
                        },
                    )
                uploaded.raise_for_status()
                result = uploaded.json()
            if not isinstance(result, dict) or not isinstance(result.get("id"), str):
                raise ToolExecutionError("YouTube upload returned no video ID.")
            return result
        except ToolExecutionError:
            raise
        except (OSError, httpx.HTTPError, ValueError, TypeError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" (HTTP {status})" if status else ""
            raise ToolExecutionError(
                f"YouTube video upload failed{suffix}. Check upload scope, file format, size and quota."
            ) from exc


class YouTubeChannelInput(StrictModel):
    pass


class YouTubeListVideosInput(StrictModel):
    max_results: int = Field(default=10, ge=1, le=50)


class YouTubeListCommentsInput(StrictModel):
    video_id: str = Field(min_length=6, max_length=64)
    max_results: int = Field(default=20, ge=1, le=100)


class YouTubeReplyCommentInput(StrictModel):
    parent_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=10000)


class YouTubeUploadInput(StrictModel):
    file_path: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    category_id: str = Field(default="22", min_length=1, max_length=8)
    privacy_status: Literal["private", "unlisted", "public"] = "private"


class YouTubeGetChannelTool(Tool):
    name = "youtube.get_channel"
    description = "Read the authenticated YouTube channel's identity, subscriber and upload-playlist details."
    permission_scope = "youtube.read"
    risk = Risk.LOW
    autonomy_level = 0
    timeout_seconds = 30
    idempotent = True
    returns_untrusted = True
    input_model = YouTubeChannelInput

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    def run_sync(self, args: YouTubeChannelInput, ctx: ToolContext) -> dict[str, Any]:
        payload = self.client.request(
            "GET", "channels", params={"part": "snippet,contentDetails,statistics", "mine": "true"}
        )
        items = payload.get("items") or []
        if not items:
            raise ToolExecutionError("No YouTube channel is available for this Google account.")
        item = items[0]
        snippet = item.get("snippet") or {}
        content = item.get("contentDetails") or {}
        statistics = item.get("statistics") or {}
        return {
            "channel_id": item.get("id"),
            "title": snippet.get("title"),
            "description": str(snippet.get("description") or "")[:1000],
            "uploads_playlist_id": (content.get("relatedPlaylists") or {}).get("uploads"),
            "subscriber_count": statistics.get("subscriberCount"),
            "video_count": statistics.get("videoCount"),
            "view_count": statistics.get("viewCount"),
        }

    def verify(self, args: YouTubeChannelInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("channel_id"))
        return VerificationEvidence(ok=ok, confidence=0.98 if ok else 0.0, checks=["authenticated_channel_id_present"])


class YouTubeListVideosTool(Tool):
    name = "youtube.list_videos"
    description = "List recent uploads from the authenticated YouTube channel, including titles, status and view counts."
    permission_scope = "youtube.read"
    risk = Risk.LOW
    autonomy_level = 0
    timeout_seconds = 45
    idempotent = True
    returns_untrusted = True
    input_model = YouTubeListVideosInput

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    def run_sync(self, args: YouTubeListVideosInput, ctx: ToolContext) -> dict[str, Any]:
        channel = self.client.request(
            "GET", "channels", params={"part": "contentDetails", "mine": "true"}
        )
        items = channel.get("items") or []
        if not items:
            raise ToolExecutionError("No YouTube channel is available for this Google account.")
        playlist_id = ((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        if not playlist_id:
            return {"videos": [], "count": 0}
        playlist = self.client.request(
            "GET", "playlistItems",
            params={"part": "contentDetails,snippet", "playlistId": playlist_id, "maxResults": args.max_results},
        )
        ids = [
            item.get("contentDetails", {}).get("videoId")
            for item in playlist.get("items", [])
            if item.get("contentDetails", {}).get("videoId")
        ]
        if not ids:
            return {"videos": [], "count": 0}
        details = self.client.request(
            "GET", "videos",
            params={"part": "snippet,statistics,status", "id": ",".join(ids)},
        )
        videos = []
        for item in details.get("items", []):
            snippet = item.get("snippet") or {}
            videos.append({
                "video_id": item.get("id"),
                "title": snippet.get("title"),
                "description": str(snippet.get("description") or "")[:500],
                "published_at": snippet.get("publishedAt"),
                "privacy_status": (item.get("status") or {}).get("privacyStatus"),
                "view_count": (item.get("statistics") or {}).get("viewCount"),
                "like_count": (item.get("statistics") or {}).get("likeCount"),
                "comment_count": (item.get("statistics") or {}).get("commentCount"),
            })
        return {"videos": videos, "count": len(videos)}

    def verify(self, args: YouTubeListVideosInput, output: dict[str, Any]) -> VerificationEvidence:
        return VerificationEvidence(ok=isinstance(output.get("videos"), list), confidence=0.98, checks=["video_list_returned"])


class YouTubeListCommentsTool(Tool):
    name = "youtube.list_comments"
    description = "Read top-level comments and replies on a specified YouTube video."
    permission_scope = "youtube.read"
    risk = Risk.LOW
    autonomy_level = 0
    timeout_seconds = 30
    idempotent = True
    returns_untrusted = True
    input_model = YouTubeListCommentsInput

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    def run_sync(self, args: YouTubeListCommentsInput, ctx: ToolContext) -> dict[str, Any]:
        payload = self.client.request(
            "GET", "commentThreads",
            params={
                "part": "snippet,replies", "videoId": args.video_id,
                "maxResults": args.max_results, "textFormat": "plainText",
            },
        )
        comments = []
        for item in payload.get("items", []):
            snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            comment = item.get("snippet", {}).get("topLevelComment", {})
            comments.append({
                "comment_id": comment.get("id"),
                "author": snippet.get("authorDisplayName"),
                "text": str(snippet.get("textDisplay") or "")[:2000],
                "like_count": snippet.get("likeCount"),
                "published_at": snippet.get("publishedAt"),
                "reply_count": item.get("snippet", {}).get("totalReplyCount", 0),
            })
        return {"video_id": args.video_id, "comments": comments, "count": len(comments)}

    def verify(self, args: YouTubeListCommentsInput, output: dict[str, Any]) -> VerificationEvidence:
        return VerificationEvidence(ok=isinstance(output.get("comments"), list), confidence=0.95, checks=["comments_list_returned"])


class YouTubeReplyCommentTool(Tool):
    name = "youtube.reply_to_comment"
    description = "Post a public reply to a YouTube comment. Requires youtube.write and explicit owner approval."
    permission_scope = "youtube.write"
    risk = Risk.HIGH
    autonomy_level = 2
    timeout_seconds = 30
    idempotent = False
    input_model = YouTubeReplyCommentInput

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    def run_sync(self, args: YouTubeReplyCommentInput, ctx: ToolContext) -> dict[str, Any]:
        payload = self.client.request(
            "POST", "comments", params={"part": "snippet"},
            payload={"snippet": {"parentId": args.parent_id, "textOriginal": args.text}},
        )
        comment_id = payload.get("id")
        if not isinstance(comment_id, str):
            raise ToolExecutionError("YouTube did not confirm the posted comment reply.")
        return {"comment_id": comment_id, "parent_id": args.parent_id, "posted": True}

    def verify(self, args: YouTubeReplyCommentInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("comment_id")) and output.get("posted") is True
        return VerificationEvidence(ok=ok, confidence=0.99 if ok else 0.0, checks=["reply_id_returned"])


class YouTubeUploadVideoTool(Tool):
    name = "youtube.upload_video"
    description = "Upload a video from an approved local folder. Defaults to private; public publishing requires owner approval."
    permission_scope = "youtube.write"
    risk = Risk.HIGH
    autonomy_level = 2
    timeout_seconds = 600
    idempotent = False
    input_model = YouTubeUploadInput

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    def run_sync(self, args: YouTubeUploadInput, ctx: ToolContext) -> dict[str, Any]:
        path = ctx.resolve_path(args.file_path, must_exist=True)
        if not path.is_file():
            raise ToolExecutionError("The selected upload path is not a regular file.")
        size = path.stat().st_size
        if size <= 0 or size > MAX_VIDEO_BYTES:
            raise ToolExecutionError("Video file must be between 1 byte and 512 MiB.")
        result = self.client.upload(path, args.model_dump())
        video_id = result.get("id")
        status = result.get("status") or {}
        return {
            "video_id": video_id,
            "title": (result.get("snippet") or {}).get("title", args.title),
            "privacy_status": status.get("privacyStatus", args.privacy_status),
            "file_name": path.name,
            "uploaded": bool(video_id),
        }

    def verify(self, args: YouTubeUploadInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("video_id")) and output.get("uploaded") is True
        return VerificationEvidence(
            ok=ok,
            confidence=0.99 if ok else 0.0,
            checks=["youtube_video_id_returned"],
            detail={"privacy_status": output.get("privacy_status")},
        )


def build_youtube_tools(config: MatrixConfig) -> list[Tool]:
    client = YouTubeClient(config)
    return [
        YouTubeGetChannelTool(client),
        YouTubeListVideosTool(client),
        YouTubeListCommentsTool(client),
        YouTubeReplyCommentTool(client),
        YouTubeUploadVideoTool(client),
    ]
