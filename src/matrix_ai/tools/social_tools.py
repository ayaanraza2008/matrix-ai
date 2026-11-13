"""Approval-gated LinkedIn, Instagram and WhatsApp Cloud API tools."""
from __future__ import annotations
import re
import httpx
from pydantic import Field, field_validator
from ..core.models import Risk, VerificationEvidence
from ..errors import ToolExecutionError, ToolUnavailable
from .base import StrictModel, Tool, ToolContext

def _verified(resp, allowed=(200,201)):
    if resp.status_code in (401,403): raise ToolUnavailable("Provider rejected credentials or required permissions.")
    if resp.status_code not in allowed:
        raise ToolExecutionError(f"Social provider returned HTTP {resp.status_code}.")
    try: return resp.json()
    except ValueError as exc: raise ToolExecutionError("Social provider returned invalid JSON.") from exc
def _token(token,name):
    if not token: raise ToolUnavailable(f"Configure {name} before using this tool.")
    return token
def _https_url(value):
    if not value.startswith("https://") or len(value)>2048: raise ValueError("image_url must be a public HTTPS URL")
    return value
class _SocialTool(Tool):
    timeout_seconds=20.0
    def verify(self,args,output):
        ok=bool(output.get("post_id") or output.get("reply_id") or output.get("message_id") or output.get("media_id") or output.get("comments") is not None)
        return VerificationEvidence(ok=ok,confidence=.9 if ok else 0,checks=["provider_identifier_or_read_result"])

class LinkedInPostInput(StrictModel):
    text: str=Field(min_length=1,max_length=3000)
    visibility: str=Field(default="PUBLIC",pattern="^(PUBLIC|CONNECTIONS)$")
class LinkedInCreatePostTool(_SocialTool):
    name="linkedin_create_post"; description="Publish a LinkedIn post."; permission_scope="linkedin.write"; risk=Risk.HIGH; idempotent=False; input_model=LinkedInPostInput
    def run_sync(self,args,ctx):
        token=_token(ctx.config.linkedin_access_token,"MATRIX_LINKEDIN_ACCESS_TOKEN")
        author=ctx.config.linkedin_author_urn or ""
        if not re.fullmatch(r"urn:li:(person|organization):[A-Za-z0-9_-]+",author): raise ToolExecutionError("Configure a valid MATRIX_LINKEDIN_AUTHOR_URN.")
        payload={"author":author,"commentary":args.text,"visibility":args.visibility,"distribution":{"feedDistribution":"MAIN_FEED","targetEntities":[]},"lifecycleState":"PUBLISHED","isReshareDisabledByAuthor":False}
        try:
            r=httpx.post("https://api.linkedin.com/rest/posts",headers={"Authorization":f"Bearer {token}","LinkedIn-Version":ctx.config.linkedin_api_version,"X-Restli-Protocol-Version":"2.0.0","Content-Type":"application/json"},json=payload,timeout=12)
        except httpx.HTTPError as exc: raise ToolExecutionError("LinkedIn post request failed.") from exc
        _verified(r)
        post_id=r.headers.get("x-restli-id")
        if not post_id: raise ToolExecutionError("LinkedIn did not confirm the created post ID.")
        return {"post_id":post_id}

class InstagramMediaInput(StrictModel):
    media_id: str=Field(min_length=1,max_length=100)
    limit: int=Field(default=20,ge=1,le=50)
class InstagramReplyInput(StrictModel):
    comment_id: str=Field(min_length=1,max_length=100)
    message: str=Field(min_length=1,max_length=1000)
class InstagramPublishInput(StrictModel):
    image_url: str
    caption: str=Field(default="",max_length=2200)
    @field_validator("image_url")
    @classmethod
    def https_only(cls,v): return _https_url(v)
class _InstagramTool(_SocialTool):
    def _base(self,ctx):
        token=_token(ctx.config.instagram_access_token,"MATRIX_INSTAGRAM_ACCESS_TOKEN")
        version=ctx.config.meta_graph_version or "v24.0"
        return token,f"https://graph.facebook.com/{version}"
    def _headers(self,token): return {"Authorization":f"Bearer {token}"}
class InstagramListCommentsTool(_InstagramTool):
    name="instagram_list_comments"; description="Read bounded comments on Instagram media."; permission_scope="instagram.read"; risk=Risk.LOW; idempotent=True; input_model=InstagramMediaInput
    def run_sync(self,args,ctx):
        token,base=self._base(ctx)
        try: r=httpx.get(f"{base}/{args.media_id}/comments",headers=self._headers(token),params={"limit":args.limit,"fields":"id,text,username,timestamp"},timeout=12)
        except httpx.HTTPError as exc: raise ToolExecutionError("Instagram comments request failed.") from exc
        d=_verified(r); comments=[]
        for x in d.get("data",[])[:args.limit]:
            comments.append({k:x.get(k) for k in ("id","text","username","timestamp")})
        return {"count":len(comments),"comments":comments}
class InstagramReplyCommentTool(_InstagramTool):
    name="instagram_reply_comment"; description="Reply to an Instagram comment."; permission_scope="instagram.write"; risk=Risk.HIGH; idempotent=False; input_model=InstagramReplyInput
    def run_sync(self,args,ctx):
        token,base=self._base(ctx)
        try: r=httpx.post(f"{base}/{args.comment_id}/replies",headers=self._headers(token),data={"message":args.message},timeout=12)
        except httpx.HTTPError as exc: raise ToolExecutionError("Instagram reply request failed.") from exc
        d=_verified(r)
        if not d.get("id"): raise ToolExecutionError("Instagram did not confirm the reply ID.")
        return {"reply_id":d["id"]}
class InstagramPublishImageTool(_InstagramTool):
    name="instagram_publish_image"; description="Publish an HTTPS image to the connected Instagram account."; permission_scope="instagram.publish"; risk=Risk.CRITICAL; idempotent=False; input_model=InstagramPublishInput
    def run_sync(self,args,ctx):
        token,base=self._base(ctx); user=ctx.config.instagram_user_id
        if not user: raise ToolUnavailable("Configure MATRIX_INSTAGRAM_USER_ID.")
        headers=self._headers(token)
        try:
            first=httpx.post(f"{base}/{user}/media",headers=headers,data={"image_url":args.image_url,"caption":args.caption},timeout=20)
            container=_verified(first).get("id")
            if not container: raise ToolExecutionError("Instagram did not confirm media container creation.")
            second=httpx.post(f"{base}/{user}/media_publish",headers=headers,data={"creation_id":container},timeout=20)
        except httpx.HTTPError as exc: raise ToolExecutionError("Instagram publish request failed.") from exc
        media=_verified(second).get("id")
        if not media: raise ToolExecutionError("Instagram did not confirm publication.")
        return {"media_id":media}
class WhatsAppTextInput(StrictModel):
    recipient: str
    text: str=Field(min_length=1,max_length=4096)
    @field_validator("recipient")
    @classmethod
    def e164(cls,v):
        if not re.fullmatch(r"\+?[1-9][0-9]{7,14}",v): raise ValueError("recipient must be an E.164 phone number")
        return v
class WhatsAppTemplateInput(StrictModel):
    recipient: str
    template_name: str=Field(min_length=1,max_length=100,pattern="^[a-z0-9_]+$")
    language_code: str=Field(default="en_US",pattern="^[a-z]{2}_[A-Z]{2}$")
    body_parameters: list[str]=Field(default_factory=list,max_length=10)
    @field_validator("recipient")
    @classmethod
    def e164(cls,v):
        if not re.fullmatch(r"\+?[1-9][0-9]{7,14}",v): raise ValueError("recipient must be an E.164 phone number")
        return v
    @field_validator("body_parameters")
    @classmethod
    def bounded(cls,v):
        if any(len(x)>1024 for x in v): raise ValueError("template parameter exceeds 1024 characters")
        return v
class _WhatsAppTool(_SocialTool):
    def _endpoint(self,ctx):
        token=_token(ctx.config.whatsapp_access_token,"MATRIX_WHATSAPP_ACCESS_TOKEN")
        phone=ctx.config.whatsapp_phone_number_id
        if not phone: raise ToolUnavailable("Configure MATRIX_WHATSAPP_PHONE_NUMBER_ID.")
        version=ctx.config.meta_graph_version or "v24.0"
        return token,f"https://graph.facebook.com/{version}/{phone}/messages"
    def _send(self,ctx,payload):
        token,url=self._endpoint(ctx)
        try: r=httpx.post(url,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},json=payload,timeout=15)
        except httpx.HTTPError as exc: raise ToolExecutionError("WhatsApp send request failed.") from exc
        d=_verified(r); messages=d.get("messages") or []
        if not messages or not messages[0].get("id"): raise ToolExecutionError("WhatsApp did not confirm the message ID.")
        return {"message_id":messages[0]["id"]}
class WhatsAppSendTextTool(_WhatsAppTool):
    name="whatsapp_send_text"; description="Send a WhatsApp text message."; permission_scope="whatsapp.write"; risk=Risk.HIGH; idempotent=False; input_model=WhatsAppTextInput
    def run_sync(self,args,ctx):
        return self._send(ctx,{"messaging_product":"whatsapp","recipient_type":"individual","to":args.recipient.lstrip("+"),"type":"text","text":{"preview_url":False,"body":args.text}})
class WhatsAppSendTemplateTool(_WhatsAppTool):
    name="whatsapp_send_template"; description="Send an approved WhatsApp template."; permission_scope="whatsapp.write"; risk=Risk.HIGH; idempotent=False; input_model=WhatsAppTemplateInput
    def run_sync(self,args,ctx):
        components=[]
        if args.body_parameters:
            components=[{"type":"body","parameters":[{"type":"text","text":v} for v in args.body_parameters]}]
        return self._send(ctx,{"messaging_product":"whatsapp","to":args.recipient.lstrip("+"),"type":"template","template":{"name":args.template_name,"language":{"code":args.language_code},"components":components}})
