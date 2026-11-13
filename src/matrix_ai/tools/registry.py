"""Tool registry: the single allowlist of capabilities MATRIX can execute."""

from __future__ import annotations

from collections.abc import Iterable

from ..core.models import Risk, ToolSpec
from ..errors import ToolNotFound
from .app_tools import CloseAppTool, OpenAppTool
from .base import Tool, ToolContext
from .file_tools import (
    DeleteFileTool,
    ListFilesTool,
    MovePathTool,
    ReadTextTool,
    StatPathTool,
    WriteTextTool,
)
from .github_tools import (
    GitHubCommentIssueTool,
    GitHubCreateIssueTool,
    GitHubGetPullRequestTool,
    GitHubGetRepositoryTool,
    GitHubListIssuesTool,
    GitHubWhoAmITool,
)
from .memory_tools import MemorySaveTool, MemorySearchTool
from .shell_tools import RunAllowedCommandTool
from .social_tools import (
    InstagramListCommentsTool,
    InstagramPublishImageTool,
    InstagramReplyCommentTool,
    LinkedInCreatePostTool,
    WhatsAppSendTemplateTool,
    WhatsAppSendTextTool,
)
from .system_tools import SystemStatusTool
from .vision_tools import OcrImageTool
from .weather_tools import OpenMeteoForecastTool
from .web_tools import FetchPublicUrlTool
from .youtube_tools import build_youtube_tools


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or ():
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if not tool.name or not isinstance(tool.name, str):
            raise ValueError("tool name is required")
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        if not tool.permission_scope or "." not in tool.permission_scope:
            raise ValueError(f"tool {tool.name} needs a 'domain.action' permission scope")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """Remove a dynamically connected tool; built-in tools are untouched."""
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFound(f"tool '{name}' is not registered")
        return tool

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def catalog(self) -> list[ToolSpec]:
        return [self._tools[name].spec() for name in self.names()]

    def specs(self) -> dict[str, ToolSpec]:
        return {name: tool.spec() for name, tool in self._tools.items()}

    def risk_for(self, name: str, args, ctx: ToolContext | None = None) -> Risk:
        return self.get(name).risk_for(args, ctx)

    def __len__(self) -> int:
        return len(self._tools)


def build_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            SystemStatusTool(),
            ListFilesTool(),
            ReadTextTool(),
            StatPathTool(),
            WriteTextTool(),
            DeleteFileTool(),
            MovePathTool(),
            OpenAppTool(),
            CloseAppTool(),
            RunAllowedCommandTool(),
            MemorySearchTool(),
            MemorySaveTool(),
            FetchPublicUrlTool(),
            OcrImageTool(),
            OpenMeteoForecastTool(),
            *build_youtube_tools(),
            LinkedInCreatePostTool(),
            InstagramListCommentsTool(),
            InstagramPublishImageTool(),
            InstagramReplyCommentTool(),
            WhatsAppSendTextTool(),
            WhatsAppSendTemplateTool(),
            GitHubWhoAmITool(),
            GitHubGetRepositoryTool(),
            GitHubListIssuesTool(),
            GitHubGetPullRequestTool(),
            GitHubCreateIssueTool(),
            GitHubCommentIssueTool(),
        ]
    )
