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

