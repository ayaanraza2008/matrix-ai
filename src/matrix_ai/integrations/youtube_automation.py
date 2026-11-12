"""Safe, typed adapter for the local YouTube Automation Agent service.

The upstream service owns OAuth, media generation, channel state and analytics.
MATRIX only exposes a fixed route allowlist; no model-supplied URL/path is
accepted. Generation and publishing operations are high/critical risk.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from pydantic import Field

from ..core.models import Risk, VerificationEvidence
from ..errors import ToolExecutionError, ToolUnavailable
from ..models.base import is_local_endpoint
from ..tools.base import StrictModel, Tool, ToolContext


class EmptyInput(StrictModel):
    pass


class GenerateVideoInput(StrictModel):
    topic: str = Field(min_length=3, max_length=500)
    style: str = Field(default="tutorial", min_length=1, max_length=80)


class JobInput(StrictModel):
    job_id: str = Field(min_length=1, max_length=160)


class ResumeJobInput(StrictModel):
    job_id: str = Field(min_length=1, max_length=160)
    stage: str | None = Field(default=None, max_length=80)


class ProductionInput(StrictModel):
    production_id: str = Field(min_length=1, max_length=160)


class ContentApprovalInput(StrictModel):
    production_id: str = Field(min_length=1, max_length=160)
    privacy_status: str = Field(default="private", pattern="^(private|unlisted|public)$")
    fact_checked: bool
    rights_confirmed: bool


def _make_youtube_tool(config, *, name: str, description: str, input_model, risk: Risk, operation: str) -> Tool:
    base_url = config.youtube_automation_url.rstrip("/")
    if not is_local_endpoint(base_url):
        raise ToolUnavailable("YouTube automation API must be a loopback/private service.")
    api_key = config.youtube_automation_api_key

    class YouTubeTool(Tool):
        permission_scope = "addons.read" if risk is Risk.LOW else "addons.control"
        autonomy_level = 1 if risk is Risk.LOW else 2
        idempotent = risk is Risk.LOW
        returns_untrusted = True
        timeout_seconds = 120.0
        requires_verification = True

        def run_sync(self, args, ctx):
            raise ToolExecutionError("YouTube API tools execute asynchronously through MATRIX")

        async def run(self, args, ctx: ToolContext) -> dict[str, Any]:
            ctx.ensure_active()
            payload = args.model_dump(mode="json", exclude_none=True)
            method = "GET"
            path = ""
            body = None
            if operation == "generate":
                method, path, body = "POST", "/generate", {"topic": payload["topic"], "style": payload["style"]}
            elif operation == "dashboard":
                method, path = "GET", "/api/dashboard"
            elif operation == "readiness":
                method, path = "GET", "/api/readiness"
            elif operation == "analytics":
                method, path = "GET", "/api/analytics/channel"
            elif operation == "job":
                method, path = "GET", f"/api/jobs/{quote(payload['job_id'], safe='')}"
            elif operation == "resume":
                method, path, body = "POST", f"/api/jobs/{quote(payload['job_id'], safe='')}/resume", {}
                if payload.get("stage"):
                    body["stage"] = payload["stage"]
            elif operation == "operator_start":
                method, path, body = "POST", "/api/operator/start", {}
            elif operation == "publish":
                method, path, body = "POST", f"/api/content/{quote(payload['production_id'], safe='')}/publish-now", {}
            elif operation == "approve_content":
                method, path, body = (
                    "POST",
                    f"/api/content/{quote(payload['production_id'], safe='')}/approve",
                    {
                        "privacyStatus": payload["privacy_status"],
                        "factChecked": payload["fact_checked"],
                        "rightsConfirmed": payload["rights_confirmed"],
                    },
                )
            else:
                raise ToolExecutionError("Unknown YouTube adapter operation")
            headers = {"Accept": "application/json"}
            if api_key:
                headers["x-api-key"] = api_key
            try:
                async with httpx.AsyncClient(
                    timeout=120.0,
                    follow_redirects=False,
                    headers=headers,
                ) as client:
                    response = await client.request(
                        method, f"{base_url}{path}", json=body
                    )
            except httpx.HTTPError as exc:
                raise ToolUnavailable(f"YouTube automation service unreachable: {type(exc).__name__}") from exc
            if not 200 <= response.status_code < 300:
                raise ToolExecutionError(f"YouTube automation service returned HTTP {response.status_code}")
            try:
                data = response.json()
            except ValueError:
                data = {"text": response.text[:6000]}
            if isinstance(data, dict) and (data.get("error") or data.get("success") is False):
                raise ToolExecutionError(str(data.get("error") or "YouTube automation action failed")[:500])
            output = {
                "service": "youtube-automation-agent",
                "operation": operation,
                "status_code": response.status_code,
                "result": data if isinstance(data, (dict, list)) else str(data)[:6000],
                "suspicious": True,
                "untrusted": True,
            }
            return output

        def verify(self, args, output: dict[str, Any]) -> VerificationEvidence:
            ok = isinstance(output, dict) and 200 <= int(output.get("status_code", 0)) < 300
            return VerificationEvidence(
                ok=ok,
                confidence=0.55 if ok else 0.0,
                checks=["youtube_service_accepted_request" if ok else "youtube_service_did_not_confirm"],
                detail={
                    "operation": operation,
                    "postcondition": "service_response_only; not proof of publication or audience delivery",
                },
            )

    YouTubeTool.risk = risk
    YouTubeTool.input_model = input_model
    YouTubeTool.__name__ = f"YouTube_{name.replace('.', '_')}"
    YouTubeTool.name = name
    YouTubeTool.description = description
    return YouTubeTool()


def build_youtube_tools(config) -> list[Tool]:
    if not config.youtube_automation_enabled:
        return []
    return [
        _make_youtube_tool(
            config, name="youtube.dashboard",
            description="Read the local YouTube Automation Agent dashboard and automation state.",
            input_model=EmptyInput, risk=Risk.LOW, operation="dashboard",
        ),
        _make_youtube_tool(
            config, name="youtube.readiness",
            description="Read production readiness, connected providers and channel configuration.",
            input_model=EmptyInput, risk=Risk.LOW, operation="readiness",
        ),
        _make_youtube_tool(
            config, name="youtube.analytics",
            description="Read whole-channel YouTube analytics from the configured local automation service.",
            input_model=EmptyInput, risk=Risk.LOW, operation="analytics",
        ),
        _make_youtube_tool(
            config, name="youtube.job_status",
            description="Read the status and checkpoint progress of one YouTube generation job.",
            input_model=JobInput, risk=Risk.LOW, operation="job",
        ),
        _make_youtube_tool(
            config, name="youtube.generate_video",
            description="Queue a YouTube video generation job. Requires exact MATRIX approval because it consumes provider/API quota.",
            input_model=GenerateVideoInput, risk=Risk.HIGH, operation="generate",
        ),
        _make_youtube_tool(
            config, name="youtube.resume_job",
            description="Resume an interrupted video job from its saved checkpoint. Requires owner approval.",
            input_model=ResumeJobInput, risk=Risk.HIGH, operation="resume",
        ),
        _make_youtube_tool(
            config, name="youtube.start_operator",
            description="Start the autonomous YouTube channel operator. Requires exact critical approval.",
            input_model=EmptyInput, risk=Risk.CRITICAL, operation="operator_start",
        ),
        _make_youtube_tool(
            config, name="youtube.approve_content",
            description="Approve fact-checked, rights-cleared content for scheduling. Requires exact critical approval.",
            input_model=ContentApprovalInput, risk=Risk.CRITICAL, operation="approve_content",
        ),
        _make_youtube_tool(
            config, name="youtube.publish_now",
            description="Publish a YouTube production immediately. Requires exact critical approval.",
            input_model=ProductionInput, risk=Risk.CRITICAL, operation="publish",
        ),
    ]


__all__ = ["build_youtube_tools"]
