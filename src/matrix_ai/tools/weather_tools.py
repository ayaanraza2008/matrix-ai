"""Bounded, read-only Open-Meteo forecast tool."""
from __future__ import annotations
import httpx
from pydantic import Field
from ..core.models import Risk, VerificationEvidence
from ..errors import ToolExecutionError
from .base import StrictModel, Tool, ToolContext

class OpenMeteoForecastInput(StrictModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    forecast_days: int = Field(default=3, ge=1, le=7)

class OpenMeteoForecastTool(Tool):
    name = "weather_forecast"
    description = "Get a bounded current weather and daily forecast from Open-Meteo."
    permission_scope = "weather.read"
    risk = Risk.LOW
    idempotent = True
    timeout_seconds = 12.0
    input_model = OpenMeteoForecastInput

    def run_sync(self, args: OpenMeteoForecastInput, ctx: ToolContext) -> dict:
        params = {
            "latitude": args.latitude, "longitude": args.longitude,
            "current": "temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": args.forecast_days, "timezone": "auto",
        }
        try:
            response = httpx.get("https://api.open-meteo.com/v1/forecast", params=params,
                                 timeout=httpx.Timeout(10.0, connect=4.0), headers={"Accept":"application/json"})
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise ToolExecutionError("Open-Meteo forecast request failed.") from exc
        current, daily = data.get("current"), data.get("daily")
        required = ("time", "weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_probability_max")
        if not isinstance(current, dict) or "temperature_2m" not in current or not isinstance(daily, dict) or any(k not in daily for k in required):
            raise ToolExecutionError("Open-Meteo returned an incomplete forecast.")
        dates = daily["time"]
        if not isinstance(dates, list) or len(dates) > 7 or any(not isinstance(daily[k], list) or len(daily[k]) != len(dates) for k in required[1:]):
            raise ToolExecutionError("Open-Meteo returned an invalid daily forecast.")
        forecast = []
        for i, day in enumerate(dates):
            forecast.append({"date": day, "weather_code": daily["weather_code"][i],
                "temperature_max": daily["temperature_2m_max"][i], "temperature_min": daily["temperature_2m_min"][i],
                "precipitation_probability": daily["precipitation_probability_max"][i]})
        return {"provider":"Open-Meteo","timezone":str(data.get("timezone",""))[:80],
                "current":{"temperature":current["temperature_2m"],"weather_code":current.get("weather_code")},
                "forecast":forecast}

    def verify(self, args, output):
        ok = output.get("provider") == "Open-Meteo" and bool(output.get("forecast"))
        return VerificationEvidence(ok=ok, confidence=0.9 if ok else 0.0, checks=["bounded_daily_forecast"])
