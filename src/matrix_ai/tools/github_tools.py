"""Least-privilege GitHub REST tools; writes are approval-gated by risk and scope."""
from __future__ import annotations
import re
import httpx
from pydantic import Field, field_validator
from ..core.models import Risk, VerificationEvidence
from ..errors import ToolExecutionError, ToolUnavailable, ValidationFailure
from .base import StrictModel, Tool, ToolContext

_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
def _repo(v):
    if not isinstance(v,str) or not _REPO.fullmatch(v) or ".." in v or "?" in v or "#" in v:
        raise ValueError("repository must be owner/name without query parameters")
    return v

class GitHubEmptyInput(StrictModel): pass
class GitHubRepositoryInput(StrictModel):
    repository: str
    @field_validator("repository")
    @classmethod
    def valid_repo(cls,v): return _repo(v)
class GitHubIssuesInput(GitHubRepositoryInput):
    per_page: int = Field(default=20, ge=1, le=50)
    state: str = Field(default="open", pattern="^(open|closed|all)$")
class GitHubPullRequestInput(GitHubRepositoryInput):
    number: int = Field(ge=1)
class GitHubCreateIssueInput(GitHubRepositoryInput):
    title: str = Field(min_length=1,max_length=200)
    body: str = Field(default="",max_length=60000)
    labels: list[str] = Field(default_factory=list,max_length=10)
class GitHubCommentInput(GitHubRepositoryInput):
    number: int = Field(ge=1)
    body: str = Field(min_length=1,max_length=60000)

class _GitHubTool(Tool):
    timeout_seconds=12.0
    def _headers(self,ctx):
        token=ctx.config.github_token
        if not token: raise ToolUnavailable("Set MATRIX_GITHUB_TOKEN to enable GitHub tools.")
        return {"Authorization":f"Bearer {token}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","User-Agent":"MATRIX-AI"}
    def _request(self,method,path,ctx,*,params=None,payload=None,expected=(200,)):
        try:
            response=getattr(httpx,method)(f"https://api.github.com{path}",headers=self._headers(ctx),params=params,json=payload,timeout=httpx.Timeout(10.0,connect=4.0))
        except httpx.HTTPError as exc: raise ToolExecutionError("GitHub request failed; request credentials were not logged.") from exc
        if response.status_code not in expected:
            if response.status_code in (401,403,404,429) or response.status_code>=500:
                raise ToolUnavailable("GitHub request unavailable; check token permissions, repository access, and rate limits.")
            raise ToolExecutionError(f"GitHub returned HTTP {response.status_code}.")
        try: return response.json(),response.headers
        except ValueError as exc: raise ToolExecutionError("GitHub returned invalid JSON.") from exc
    def verify(self,args,output):
        ok=bool(output.get("data") or output.get("number") or output.get("id"))
        return VerificationEvidence(ok=ok,confidence=.9 if ok else 0,checks=["github_response_confirmed"])

class GitHubWhoAmITool(_GitHubTool):
    name="github_whoami"; description="Read the authenticated GitHub account's public profile."; permission_scope="github.read"; risk=Risk.LOW; idempotent=True; input_model=GitHubEmptyInput
    def run_sync(self,args,ctx):
        data,_=self._request("get","/user",ctx)
        keys=("login","name","html_url","public_repos","followers")
        return {"data":{k:data[k] for k in keys if k in data}}

class GitHubGetRepositoryTool(_GitHubTool):
    name="github_get_repository"; description="Read repository metadata."; permission_scope="github.read"; risk=Risk.LOW; idempotent=True; input_model=GitHubRepositoryInput
    def run_sync(self,args,ctx):
        data,_=self._request("get",f"/repos/{args.repository}",ctx)
        keys=("full_name","private","html_url","default_branch","description","stargazers_count","open_issues_count")
        return {"data":{k:data[k] for k in keys if k in data}}

class GitHubListIssuesTool(_GitHubTool):
    name="github_list_issues"; description="List bounded GitHub issues, excluding pull requests."; permission_scope="github.read"; risk=Risk.LOW; idempotent=True; input_model=GitHubIssuesInput
    def run_sync(self,args,ctx):
        data,_=self._request("get",f"/repos/{args.repository}/issues",ctx,params={"per_page":args.per_page,"state":args.state})
        issues=[]
        for x in data if isinstance(data,list) else []:
            if "pull_request" in x: continue
            issues.append({k:x.get(k) for k in ("number","title","state","html_url","created_at")})
            issues[-1]["labels"]=[z.get("name","") for z in x.get("labels",[])[:10]]
            issues[-1]["author"]=(x.get("user") or {}).get("login")
        return {"count":len(issues),"issues":issues}

class GitHubGetPullRequestTool(_GitHubTool):
    name="github_get_pull_request"; description="Read pull request branch and merge metadata."; permission_scope="github.read"; risk=Risk.LOW; idempotent=True; input_model=GitHubPullRequestInput
    def run_sync(self,args,ctx):
        d,_=self._request("get",f"/repos/{args.repository}/pulls/{args.number}",ctx)
        return {"data":{"number":d.get("number"),"title":d.get("title"),"state":d.get("state"),"draft":d.get("draft"),"head":(d.get("head") or {}).get("ref"),"base":(d.get("base") or {}).get("ref"),"mergeable":d.get("mergeable"),"merged":d.get("merged"),"html_url":d.get("html_url")}}

class GitHubCreateIssueTool(_GitHubTool):
    name="github_create_issue"; description="Create a GitHub issue after explicit high-risk approval."; permission_scope="github.write"; risk=Risk.HIGH; idempotent=False; input_model=GitHubCreateIssueInput
    def run_sync(self,args,ctx):
        d,_=self._request("post",f"/repos/{args.repository}/issues",ctx,payload={"title":args.title,"body":args.body,"labels":args.labels},expected=(201,))
        if not d.get("number") or not d.get("html_url"): raise ToolExecutionError("GitHub did not confirm issue creation.")
        return {"data":{"number":d["number"],"title":d.get("title"),"url":d["html_url"]}}

class GitHubCommentIssueTool(_GitHubTool):
    name="github_comment_issue"; description="Post a GitHub issue comment after explicit high-risk approval."; permission_scope="github.write"; risk=Risk.HIGH; idempotent=False; input_model=GitHubCommentInput
    def run_sync(self,args,ctx):
        d,_=self._request("post",f"/repos/{args.repository}/issues/{args.number}/comments",ctx,payload={"body":args.body},expected=(201,))
        if not d.get("id") or not d.get("html_url"): raise ToolExecutionError("GitHub did not confirm comment creation.")
        return {"data":{"id":d["id"],"url":d["html_url"],"created_at":d.get("created_at")}}
