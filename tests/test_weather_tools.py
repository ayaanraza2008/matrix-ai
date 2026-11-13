from __future__ import annotations

import pytest

from matrix_ai.tools.weather_tools import OpenMeteoForecastInput, OpenMeteoForecastTool
from pydantic import ValidationError

from matrix_ai.config import MatrixConfig
from matrix_ai.core.models import DEFAULT_SCOPES, Risk
from matrix_ai.errors import ToolExecutionError
from matrix_ai.tools.base import ToolContext
from matrix_ai.tools.registry import build_registry

def context(tmp_path):
    return ToolContext(task_id="weather-test", config=MatrixConfig(home=tmp_path))


def payload():
    return {
        "latitude": 28.98,
        "longitude": 77.71,
        "timezone": "Asia/Kolkata",
        "current_units": {"temperature_2m": "°C"},
        "current": {"temperature_2m": 25.0, "weather_code": 1},
        "daily_units": {"temperature_2m_max": "°C"},
        "daily": {
            "time": ["2026-11-12", "2026-11-13"],
            "weather_code": [1, 2],
            "temperature_2m_max": [29.0, 28.0],
            "temperature_2m_min": [17.0, 16.0],
            "precipitation_probability_max": [0, 10],
        },
    }


def test_weather_tool_is_registered_with_read_only_scope():
    tool = build_registry().get("weather_forecast")
    assert isinstance(tool, OpenMeteoForecastTool)
    assert tool.permission_scope == "weather.read"
    assert tool.risk == Risk.LOW
    assert tool.spec().idempotent is True
    assert "weather.read" in DEFAULT_SCOPES


def test_weather_input_rejects_invalid_coordinates_and_unknown_fields():
    with pytest.raises(ValidationError):
        OpenMeteoForecastInput(latitude=91, longitude=0)
    with pytest.raises(ValidationError):
        OpenMeteoForecastInput(latitude=28, longitude=77, api_key="secret")


def test_weather_tool_returns_bounded_forecast(tmp_path, monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload()

    calls = {}

    def fake_get(url, *, params, timeout, headers):
        calls.update(url=url, params=params, timeout=timeout, headers=headers)
        return Response()

    monkeypatch.setattr("matrix_ai.tools.weather_tools.httpx.get", fake_get)
    tool = OpenMeteoForecastTool()
    args = tool.validate({"latitude": 28.98, "longitude": 77.71, "forecast_days": 2})
    result = tool.run_sync(args, context(tmp_path))

    assert calls["url"] == "https://api.open-meteo.com/v1/forecast"
    assert calls["params"]["forecast_days"] == 2
    assert calls["params"]["timezone"] == "auto"
    assert result["provider"] == "Open-Meteo"
    assert len(result["forecast"]) == 2
    assert tool.verify(args, result).ok is True


def test_weather_tool_rejects_incomplete_provider_response(tmp_path, monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"current": {}, "daily": {"time": ["2026-11-12"]}}

    monkeypatch.setattr("matrix_ai.tools.weather_tools.httpx.get", lambda *a, **k: Response())
    tool = OpenMeteoForecastTool()
    args = tool.validate({"latitude": 28.98, "longitude": 77.71})
    with pytest.raises(ToolExecutionError):
        tool.run_sync(args, context(tmp_path))


def test_weather_tool_wraps_network_errors(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        import httpx
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("matrix_ai.tools.weather_tools.httpx.get", fail)
    tool = OpenMeteoForecastTool()
    args = tool.validate({"latitude": 28.98, "longitude": 77.71})
    with pytest.raises(ToolExecutionError, match="Open-Meteo forecast request failed"):
        tool.run_sync(args, context(tmp_path))
