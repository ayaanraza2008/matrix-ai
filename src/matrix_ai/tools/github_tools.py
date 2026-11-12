"""Native, read-only GitHub account and repository actions."""

from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import Field

from ..core.models import Risk, VerificationEvidence
from ..errors import ToolExecutionError, ToolUnavailable
from .base import StrictModel, Tool, ToolContext

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_BASE_URL = "https://api.github.com"
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "MATRIX-AI/0.1",
}


class GitHubEmptyInput(StrictModel):
    pass


class GitHubRepositoryInput(StrictModel):
    repository: str = Field(min_length=3, max_length=201, description="owner/repository")


class GitHubIssuesInput(GitHubRepositoryInput):
    state: str = Field(default="open", pattern="^(open|closed|all)$")
    per_page: int = Field(default=10, ge=1, le=30)


class GitHubPullRequestInput(StrictModel):
    repository: str = Field(min_length=3, max_length=201, description="owner/repository")
    number: int = Field(ge=1)


class GitHubReadTool(Tool):
    """Shared transport and validation for GitHub read-only tools."""

    risk = Risk.LOW
    idempotent = True
    timeout_seconds = 12.0
    max_output_bytes = 24_000

    def request(self, ctx: ToolContext, path: str, params: dict[str, Any] | None = None) -> Any:
        token = ctx.config.github_token
        if not token:
            raise ToolUnavailable(
                "GitHub is not configured. Set MATRIX_GITHUB_TOKEN to a fine-grained, read-only token."
            )
        try:
            response = httpx.get(
                f"{_BASE_URL}{path}",
                headers={**_HEADERS, "Authorization": f"Bearer {token}"},
                params=params,
                timeout=httpx.Timeout(self.timeout_seconds, connect=5.0),
            )
        except httpx.HTTPError as exc:
            raise ToolExecutionError(f"GitHub request failed ({type(exc).__name__})") from exc

        if response.status_code == 401:
            raise ToolUnavailable("GitHub rejected MATRIX_GITHUB_TOKEN (401). Check or rotate the token.")
        if response.status_code == 403:
            remaining = response.headers.get("x-ratelimit-remaining")
            if remaining == "0":
                raise ToolUnavailable("GitHub API rate limit exhausted; retry after the reset window.")
            raise ToolUnavailable("GitHub denied this request; the token may lack read permission (403).")
        if response.status_code == 404:
            raise ToolUnavailable("GitHub resource was not found or is not visible to this token (404).")
        if response.status_code >= 500:
            raise ToolUnavailable(f"GitHub is temporarily unavailable (HTTP {response.status_code}).")
        if response.status_code != 200:
            raise ToolExecutionError(f"GitHub returned unexpected HTTP {response.status_code}.")
        try:
            return response.json()
        except ValueError as exc:
            raise ToolExecutionError("GitHub returned invalid JSON.") from exc

    @staticmethod
    def repository_path(repository: str) -> str:
        if not _REPOSITORY.fullmatch(repository):
            raise ToolExecutionError("repository must use the owner/repository format.")
        owner, name = repository.split("/", 1)
        return f"/repos/{owner}/{name}"

    def verify(self, args: StrictModel, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("ok")) and bool(output.get("data") is not None)
        return VerificationEvidence(
            ok=ok,
            confidence=0.95 if ok else 0.0,
            checks=["github_api_success", "response_data_present"],
        )


class GitHubWhoAmITool(GitHubReadTool):
    name = "github_whoami"
    description = "Read the authenticated GitHub user's public account identity."
    permission_scope = "github.read"
    input_model = GitHubEmptyInput

    def run_sync(self, args: GitHubEmptyInput, ctx: ToolContext) -> dict[str, Any]:
        ctx.ensure_active()
        data = self.request(ctx, "/user")
        return {
            "ok": True,
            "data": {
                "login": data.get("login"),
                "name": data.get("name"),
                "html_url": data.get("html_url"),
                "public_repos": data.get("public_repos"),
                "followers": data.get("followers"),
            },
        }


class GitHubGetRepositoryTool(GitHubReadTool):
    name = "github_get_repository"
    description = "Read metadata for a repository visible to the authenticated GitHub account."
    permission_scope = "github.read"
    input_model = GitHubRepositoryInput

    def run_sync(self, args: GitHubRepositoryInput, ctx: ToolContext) -> dict[str, Any]:
        ctx.ensure_active()
        data = self.request(ctx, self.repository_path(args.repository))
        return {
            "ok": True,
            "data": {
                "full_name": data.get("full_name"),
                "description": data.get("description"),
                "private": data.get("private"),
                "html_url": data.get("html_url"),
                "default_branch": data.get("default_branch"),
                "open_issues_count": data.get("open_issues_count"),
                "language": data.get("language"),
                "archived": data.get("archived"),
            },
        }


class GitHubListIssuesTool(GitHubReadTool):
    name = "github_list_issues"
    description = "List a bounded page of issues in a repository; pull requests are excluded."
    permission_scope = "github.read"
    input_model = GitHubIssuesInput

    def run_sync(self, args: GitHubIssuesInput, ctx: ToolContext) -> dict[str, Any]:
        ctx.ensure_active()
        data = self.request(
            ctx,
            f"{self.repository_path(args.repository)}/issues",
            {"state": args.state, "per_page": args.per_page, "sort": "updated", "direction": "desc"},
        )
        if not isinstance(data, list):
            raise ToolExecutionError("GitHub issues response was not a list.")
        issues = [
            {
                "number": item.get("number"),
                "title": item.get("title"),
                "state": item.get("state"),
                "html_url": item.get("html_url"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "labels": [label.get("name") for label in item.get("labels", [])[:20]],
                "user": (item.get("user") or {}).get("login"),
            }
            for item in data
            if "pull_request" not in item
        ]
        return {"ok": True, "repository": args.repository, "issues": issues, "count": len(issues)}


class GitHubGetPullRequestTool(GitHubReadTool):
    name = "github_get_pull_request"
    description = "Read a pull request's title, state, branches, merge status and checks summary."
    permission_scope = "github.read"
    input_model = GitHubPullRequestInput

    def run_sync(self, args: GitHubPullRequestInput, ctx: ToolContext) -> dict[str, Any]:
        ctx.ensure_active()
        data = self.request(ctx, f"{self.repository_path(args.repository)}/pulls/{args.number}")
        return {
            "ok": True,
            "data": {
                "number": data.get("number"),
                "title": data.get("title"),
                "state": data.get("state"),
                "draft": data.get("draft"),
                "html_url": data.get("html_url"),
                "head": (data.get("head") or {}).get("ref"),
                "base": (data.get("base") or {}).get("ref"),
                "mergeable": data.get("mergeable"),
                "merged": data.get("merged"),
                "changed_files": data.get("changed_files"),
                "additions": data.get("additions"),
                "deletions": data.get("deletions"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
            },
        }
