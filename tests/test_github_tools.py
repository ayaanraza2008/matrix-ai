from __future__ import annotations

import httpx
import pytest

from matrix_ai.tools.github_tools import (
    GitHubCommentIssueTool,
    GitHubCreateIssueTool,
    GitHubEmptyInput,
    GitHubGetPullRequestTool,
    GitHubGetRepositoryTool,
    GitHubListIssuesTool,
    GitHubWhoAmITool,
)
from matrix_ai.config import MatrixConfig
from matrix_ai.core.models import DEFAULT_SCOPES, Risk
from matrix_ai.errors import ToolExecutionError, ToolUnavailable, ValidationFailure
from matrix_ai.tools.base import ToolContext
from matrix_ai.tools.registry import build_registry

def make_context(tmp_path, token="test-token"):
    return ToolContext(
        task_id="github-test",
        config=MatrixConfig(home=tmp_path, github_token=token),
    )

def response(status=200, payload=None, headers=None):
    return httpx.Response(
        status,
        json=payload if payload is not None else {},
        headers=headers,
        request=httpx.Request("GET", "https://api.github.com/user"),
    )

def test_github_tools_registered_and_read_only():
    registry = build_registry()
    for name in (
        "github_whoami",
        "github_get_repository",
        "github_list_issues",
        "github_get_pull_request",
    ):
        tool = registry.get(name)
        assert tool.permission_scope == "github.read"
        assert tool.risk == Risk.LOW
        assert tool.spec().idempotent is True

def test_github_requires_token(tmp_path):
    tool = GitHubWhoAmITool()
    with pytest.raises(ToolUnavailable, match="MATRIX_GITHUB_TOKEN"):
        tool.run_sync(
            GitHubEmptyInput(),
            ToolContext(task_id="github-test", config=MatrixConfig(home=tmp_path, github_token=None)),
        )

def test_github_whoami_uses_bearer_token_and_returns_minimal_data(tmp_path, monkeypatch):
    calls = {}

    def fake_get(url, *, headers, params=None, timeout):
        calls.update(url=url, headers=headers, params=params)
        return response(payload={
            "login": "ayaan",
            "name": "Ayaan",
            "html_url": "https://github.com/ayaan",
            "public_repos": 12,
            "followers": 4,
            "email": "private@example.com",
        })

    monkeypatch.setattr("matrix_ai.tools.github_tools.httpx.get", fake_get)
    tool = GitHubWhoAmITool()
    result = tool.run_sync(GitHubEmptyInput(), make_context(tmp_path))
    assert calls["url"] == "https://api.github.com/user"
    assert calls["headers"]["Authorization"] == "Bearer test-token"
    assert result["data"]["login"] == "ayaan"
    assert "email" not in result["data"]
    assert tool.verify(GitHubEmptyInput(), result).ok is True

def test_github_repository_and_issues_are_bounded_and_prs_excluded(tmp_path, monkeypatch):
    def fake_get(url, *, headers, params=None, timeout):
        if url.endswith("/repos/owner/repo"):
            return response(payload={
                "full_name": "owner/repo",
                "private": True,
                "html_url": "https://github.com/owner/repo",
                "default_branch": "main",
            })
        assert params["per_page"] == 2
        return response(payload=[
            {"number": 1, "title": "Issue", "state": "open", "labels": [], "user": {"login": "u"}},
            {"number": 2, "title": "PR", "pull_request": {"url": "ignored"}},
        ])

    monkeypatch.setattr("matrix_ai.tools.github_tools.httpx.get", fake_get)
    ctx = make_context(tmp_path)
    repo_tool = GitHubGetRepositoryTool()
    repo_args = repo_tool.validate({"repository": "owner/repo"})
    assert repo_tool.run_sync(repo_args, ctx)["data"]["full_name"] == "owner/repo"

    issues_tool = GitHubListIssuesTool()
    issues_args = issues_tool.validate({"repository": "owner/repo", "per_page": 2})
    result = issues_tool.run_sync(issues_args, ctx)
    assert result["count"] == 1
    assert result["issues"][0]["number"] == 1

def test_github_pull_request_returns_branch_and_merge_metadata(tmp_path, monkeypatch):
    def fake_get(url, *, headers, params=None, timeout):
        assert url.endswith("/repos/owner/repo/pulls/7")
        return response(payload={
            "number": 7,
            "title": "Add feature",
            "state": "open",
            "draft": True,
            "head": {"ref": "feature"},
            "base": {"ref": "main"},
            "mergeable": False,
            "merged": False,
            "html_url": "https://github.com/owner/repo/pull/7",
        })

    monkeypatch.setattr("matrix_ai.tools.github_tools.httpx.get", fake_get)
    tool = GitHubGetPullRequestTool()
    args = tool.validate({"repository": "owner/repo", "number": 7})
    result = tool.run_sync(args, make_context(tmp_path))
    assert result["data"]["head"] == "feature"
    assert result["data"]["base"] == "main"
    assert result["data"]["draft"] is True

def test_github_rejects_bad_repo_and_pagination(tmp_path):
    with pytest.raises(ValidationFailure):
        GitHubGetRepositoryTool().validate({"repository": "owner/repo?per_page=100"})
    with pytest.raises(ValidationFailure):
        GitHubListIssuesTool().validate({"repository": "owner/repo", "per_page": 100})
    with pytest.raises(ValidationFailure):
        GitHubGetPullRequestTool().validate({"repository": "owner/repo?x=1", "number": 1})

@pytest.mark.parametrize(
    ("status", "error"),
    [(401, ToolUnavailable), (403, ToolUnavailable), (404, ToolUnavailable), (502, ToolUnavailable)],
)
def test_github_provider_errors_are_safely_mapped(tmp_path, monkeypatch, status, error):
    monkeypatch.setattr(
        "matrix_ai.tools.github_tools.httpx.get",
        lambda *args, **kwargs: response(status=status),
    )
    with pytest.raises(error):
        GitHubWhoAmITool().run_sync(GitHubEmptyInput(), make_context(tmp_path))

def test_github_network_error_does_not_leak_request_headers(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("matrix_ai.tools.github_tools.httpx.get", fail)
    with pytest.raises(ToolExecutionError, match="GitHub request failed"):
        GitHubWhoAmITool().run_sync(GitHubEmptyInput(), make_context(tmp_path))

def test_github_writes_are_high_risk_and_not_default_granted():
    create = GitHubCreateIssueTool()
    comment = GitHubCommentIssueTool()
    assert create.risk == Risk.HIGH
    assert comment.risk == Risk.HIGH
    assert create.permission_scope == "github.write"
    assert comment.permission_scope == "github.write"
    assert "github.write" not in DEFAULT_SCOPES
    assert create.spec().idempotent is False
    assert comment.spec().idempotent is False

def test_github_create_issue_posts_only_after_tool_execution(tmp_path, monkeypatch):
    calls = {}

    def fake_post(url, *, headers, json, timeout):
        calls.update(url=url, headers=headers, json=json)
        return response(status=201, payload={
            "number": 23,
            "title": "Bug report",
            "state": "open",
            "html_url": "https://github.com/owner/repo/issues/23",
        })

    monkeypatch.setattr("matrix_ai.tools.github_tools.httpx.post", fake_post)
    tool = GitHubCreateIssueTool()
    args = tool.validate({
        "repository": "owner/repo",
        "title": "Bug report",
        "body": "Steps to reproduce",
        "labels": ["bug"],
    })
    result = tool.run_sync(args, make_context(tmp_path))
    assert calls["url"].endswith("/repos/owner/repo/issues")
    assert calls["json"]["title"] == "Bug report"
    assert result["data"]["number"] == 23

def test_github_comment_issue_posts_to_validated_issue_path(tmp_path, monkeypatch):
    calls = {}

    def fake_post(url, *, headers, json, timeout):
        calls.update(url=url, json=json)
        return response(status=201, payload={
            "id": 99,
            "html_url": "https://github.com/owner/repo/issues/4#issuecomment-99",
            "created_at": "2026-11-12T18:00:00Z",
        })

    monkeypatch.setattr("matrix_ai.tools.github_tools.httpx.post", fake_post)
    tool = GitHubCommentIssueTool()
    args = tool.validate({"repository": "owner/repo", "number": 4, "body": "Reviewed."})
    result = tool.run_sync(args, make_context(tmp_path))
    assert calls["url"].endswith("/repos/owner/repo/issues/4/comments")
    assert calls["json"] == {"body": "Reviewed."}
    assert result["data"]["id"] == 99

def test_github_write_rejects_missing_provider_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "matrix_ai.tools.github_tools.httpx.post",
        lambda *args, **kwargs: response(status=201, payload={"number": 1}),
    )
    tool = GitHubCreateIssueTool()
    args = tool.validate({"repository": "owner/repo", "title": "No confirmation"})
    with pytest.raises(ToolExecutionError, match="did not confirm"):
        tool.run_sync(args, make_context(tmp_path))
