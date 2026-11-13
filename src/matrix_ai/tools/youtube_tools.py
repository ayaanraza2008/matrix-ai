"""Owner-authorized YouTube Data API tools. Writes are high/critical risk."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from pydantic import Field, field_validator, model_validator
from ..core.models import Risk, VerificationEvidence
from ..errors import ToolExecutionError, ToolUnavailable
from ..integrations.youtube_auth import YOUTUBE_READ_SCOPE,YOUTUBE_WRITE_SCOPE,YOUTUBE_UPLOAD_SCOPE,build_youtube_service
from .base import StrictModel,Tool,ToolContext

class EmptyInput(StrictModel): pass
class VideoIdInput(StrictModel):
    video_id: str=Field(min_length=11,max_length=20,pattern="^[A-Za-z0-9_-]+$")
class CommentInput(VideoIdInput):
    max_results: int=Field(default=20,ge=1,le=100)
class UpdateVideoInput(VideoIdInput):
    title: str|None=Field(default=None,min_length=1,max_length=100)
    description: str|None=Field(default=None,max_length=5000)
    tags: list[str]|None=Field(default=None,max_length=20)
class ReplyInput(StrictModel):
    comment_id: str=Field(min_length=1,max_length=100)
    text: str=Field(min_length=1,max_length=1000)
class YouTubeUploadVideoInput(StrictModel):
    path: str=Field(min_length=1,max_length=2000)
    title: str=Field(min_length=1,max_length=100)
    description: str=Field(default="",max_length=5000)
    tags: list[str]=Field(default_factory=list,max_length=20)
    privacy_status: str=Field(default="private",pattern="^(private|unlisted|public)$")
    publish_at: datetime|None=None
    @field_validator("publish_at")
    @classmethod
    def timezone_required(cls,v):
        if v is not None and (v.tzinfo is None or v.utcoffset() is None): raise ValueError("publish_at requires an explicit timezone")
        return v
    @model_validator(mode="after")
    def scheduled_private(self):
        if self.publish_at is not None and self.privacy_status!="private": raise ValueError("scheduled uploads must remain private until owner review")
        return self

class _YouTubeTool(Tool):
    def _service(self,ctx,scope): return build_youtube_service(ctx.config,scope)
    def verify(self,args,output):
        ok=bool(output)
        return VerificationEvidence(ok=ok,confidence=.9 if ok else 0,checks=["youtube_api_response_confirmed"])
class YouTubeGetChannelTool(_YouTubeTool):
    name="youtube.get_channel"; description="Read the connected YouTube channel."; permission_scope="youtube.read"; risk=Risk.LOW; idempotent=True; input_model=EmptyInput
    def run_sync(self,args,ctx):
        data=self._service(ctx,YOUTUBE_READ_SCOPE).channels().list(part="snippet,statistics,contentDetails",mine=True,maxResults=1).execute()
        items=data.get("items",[])
        if not items: raise ToolExecutionError("No accessible YouTube channel was returned.")
        x=items[0]; s=x.get("snippet",{}); stats=x.get("statistics",{}); content=x.get("contentDetails",{})
        return {"channel_id":x.get("id"),"title":s.get("title"),"description":s.get("description","")[:1000],"video_count":stats.get("videoCount"),"view_count":stats.get("viewCount"),"uploads_playlist":content.get("relatedPlaylists",{}).get("uploads")}
class YouTubeListCommentsTool(_YouTubeTool):
    name="youtube.list_comments"; description="Read bounded top-level comments for a video."; permission_scope="youtube.read"; risk=Risk.LOW; idempotent=True; input_model=CommentInput
    def run_sync(self,args,ctx):
        data=self._service(ctx,YOUTUBE_READ_SCOPE).commentThreads().list(part="snippet",videoId=args.video_id,maxResults=args.max_results,textFormat="plainText").execute()
        out=[]
        for x in data.get("items",[])[:args.max_results]:
            s=x.get("snippet",{}); c=s.get("topLevelComment",{}); cs=c.get("snippet",{})
            out.append({"id":c.get("id"),"text":str(cs.get("textDisplay",""))[:4000],"author":str(cs.get("authorDisplayName",""))[:120],"published_at":cs.get("publishedAt"),"like_count":cs.get("likeCount",0),"reply_count":s.get("totalReplyCount",0)})
        return {"comments":out}
class YouTubeReplyCommentTool(_YouTubeTool):
    name="youtube.reply_comment"; description="Reply to a YouTube comment."; permission_scope="youtube.write"; risk=Risk.HIGH; idempotent=False; input_model=ReplyInput
    def run_sync(self,args,ctx):
        data=self._service(ctx,YOUTUBE_WRITE_SCOPE).comments().insert(part="snippet",body={"snippet":{"parentId":args.comment_id,"textOriginal":args.text}}).execute()
        if not data.get("id"): raise ToolExecutionError("YouTube did not confirm the reply ID.")
        return {"comment_id":data["id"]}
class YouTubeUpdateVideoTool(_YouTubeTool):
    name="youtube.update_video"; description="Update selected metadata on an existing YouTube video."; permission_scope="youtube.write"; risk=Risk.HIGH; idempotent=False; input_model=UpdateVideoInput
    def run_sync(self,args,ctx):
        service=self._service(ctx,YOUTUBE_WRITE_SCOPE)
        old=service.videos().list(part="snippet,status",id=args.video_id).execute().get("items",[])
        if not old: raise ToolExecutionError("YouTube video was not found or is not accessible.")
        item=old[0]; snippet=dict(item.get("snippet",{})); status=dict(item.get("status",{}))
        if args.title is not None: snippet["title"]=args.title
        if args.description is not None: snippet["description"]=args.description
        if args.tags is not None: snippet["tags"]=args.tags
        result=service.videos().update(part="snippet,status",body={"id":args.video_id,"snippet":snippet,"status":status}).execute()
        if not result.get("id"): raise ToolExecutionError("YouTube did not confirm the video update.")
        return {"video_id":result["id"],"title":result.get("snippet",{}).get("title",snippet.get("title"))}
class YouTubeUploadVideoTool(_YouTubeTool):
    name="youtube.upload_video"; description="Upload a local video as private by default; scheduled uploads remain private."; permission_scope="youtube.upload"; risk=Risk.CRITICAL; idempotent=False; input_model=YouTubeUploadVideoInput
    def run_sync(self,args,ctx):
        path=ctx.resolve_path(args.path,must_exist=True)
        if not path.is_file() or path.stat().st_size>ctx.config.youtube_max_upload_bytes: raise ToolExecutionError("Video file is invalid or exceeds the configured upload limit.")
        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc: raise ToolUnavailable("Install the optional YouTube dependencies.") from exc
        body={"snippet":{"title":args.title,"description":args.description,"tags":args.tags},"status":{"privacyStatus":"private" if args.publish_at else args.privacy_status}}
        if args.publish_at: body["status"]["publishAt"]=args.publish_at.isoformat()
        result=self._service(ctx,YOUTUBE_UPLOAD_SCOPE).videos().insert(part="snippet,status",body=body,media_body=MediaFileUpload(str(path),chunksize=8*1024*1024,resumable=True)).execute()
        if not result.get("id"): raise ToolExecutionError("YouTube did not confirm the uploaded video ID.")
        return {"video_id":result["id"],"privacy_status":result.get("status",{}).get("privacyStatus","private")}
