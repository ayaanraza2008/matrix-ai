from __future__ import annotations

import pytest

from matrix_ai.tools.youtube_tools import (
    YouTubeGetChannelTool,
    YouTubeListCommentsTool,
    YouTubeUpdateVideoTool,
    YouTubeUploadVideoInput,
)
from pydantic import ValidationError

from matrix_ai.core.models import Risk
from matrix_ai.core.policy import PolicyEngine
from matrix_ai.errors import ToolExecutionError
from matrix_ai.integrations.youtube_auth import YOUTUBE_READ_SCOPE, build_youtube_service, connect_youtube
from matrix_ai.tools.base import ToolContext

