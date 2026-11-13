from __future__ import annotations

import pytest

from matrix_ai.tools.weather_tools import OpenMeteoForecastInput, OpenMeteoForecastTool
from pydantic import ValidationError

from matrix_ai.config import MatrixConfig
from matrix_ai.core.models import DEFAULT_SCOPES, Risk
from matrix_ai.errors import ToolExecutionError
from matrix_ai.tools.base import ToolContext
from matrix_ai.tools.registry import build_registry

