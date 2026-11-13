from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest

from matrix_ai.tools.social_tools import (
    InstagramListCommentsTool,
    InstagramPublishImageTool,
    InstagramReplyCommentTool,
    LinkedInCreatePostTool,
    WhatsAppSendTemplateTool,
    WhatsAppSendTextTool,
)
from matrix_ai.config import MatrixConfig
from matrix_ai.core.models import DEFAULT_SCOPES, Risk
from matrix_ai.errors import ToolExecutionError, ToolUnavailable, ValidationFailure
from matrix_ai.tools.base import ToolContext
from matrix_ai.tools.registry import build_registry

