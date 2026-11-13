"""Free, keyless weather forecasts from the Open-Meteo public API."""

from __future__ import annotations

import httpx
from pydantic import Field

from ..core.models import Risk, VerificationEvidence
from ..errors import ToolExecutionError
from .base import StrictModel, Tool, ToolContext


class OpenMeteoForecastInput(StrictModel):
    latitude: float = Field(ge=-90, le=90, description="Latitude in decimal degrees")
    longitude: float = Field(ge=-180, le=180, description="Longitude in decimal degrees")
    forecast_days: int = Field(default=3, ge=1, le=7)
    timezone: str = Field(default="auto", min_length=1, max_length=64)


class OpenMeteoForecastTool(Tool):
    name = "weather_forecast"
    description = (
        "Get current conditions and a 1-7 day forecast from Open-Meteo. "
        "No API key is required. Supply latitude and longitude in decimal degrees."
    )
    permission_scope = "weather.read"
    risk = Risk.LOW
    idempotent = True
    timeout_seconds = 12.0
    max_output_bytes = 20_000
    input_model = OpenMeteoForecastInput

    def run_sync(self, args: OpenMeteoForecastInput, ctx: ToolContext) -> dict:
        ctx.ensure_active()
        params = {
            "latitude": args.latitude,
            "longitude": args.longitude,
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "is_day,precipitation,weather_code,wind_speed_10m"
            ),
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max"
            ),
            "forecast_days": args.forecast_days,
            "timezone": args.timezone,
        }
        try:
            response = httpx.get(
                "https://api.open-meteo.com/v1/forecast",
                params=params,
                timeout=httpx.Timeout(self.timeout_seconds, connect=5.0),
                headers={"User-Agent": "MATRIX-AI/0.1 weather tool"},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolExecutionError(
                f"Open-Meteo forecast request failed ({type(exc).__name__})"
            ) from exc

        current = payload.get("current")
        daily = payload.get("daily")
        if not isinstance(current, dict) or not isinstance(daily, dict):
            raise ToolExecutionError("Open-Meteo returned an incomplete forecast")
        dates = daily.get("time")
        highs = daily.get("temperature_2m_max")
        lows = daily.get("temperature_2m_min")
        codes = daily.get("weather_code")
        rain = daily.get("precipitation_probability_max")
        if not all(isinstance(values, list) for values in (dates, highs, lows, codes, rain)):
            raise ToolExecutionError("Open-Meteo returned malformed daily forecast data")
        if not dates or not (len(dates) == len(highs) == len(lows) == len(codes) == len(rain)):
            raise ToolExecutionError("Open-Meteo daily forecast fields have inconsistent lengths")

        forecast = [
            {
                "date": dates[index],
                "weather_code": codes[index],
                "temperature_max_c": highs[index],
                "temperature_min_c": lows[index],
                "precipitation_probability_max_percent": rain[index],
            }
            for index in range(min(len(dates), args.forecast_days))
        ]
        return {
            "provider": "Open-Meteo",
            "source": "https://open-meteo.com/",
            "latitude": payload.get("latitude", args.latitude),
            "longitude": payload.get("longitude", args.longitude),
            "timezone": payload.get("timezone", args.timezone),
            "current_units": payload.get("current_units", {}),
            "current": current,
            "daily_units": payload.get("daily_units", {}),
            "forecast": forecast,
        }

    def verify(self, args: OpenMeteoForecastInput, output: dict) -> VerificationEvidence:
        forecast = output.get("forecast")
        ok = (
            output.get("provider") == "Open-Meteo"
            and isinstance(output.get("current"), dict)
            and isinstance(forecast, list)
            and 1 <= len(forecast) <= args.forecast_days
            and all(isinstance(day, dict) and day.get("date") for day in forecast)
        )
        return VerificationEvidence(
            ok=ok,
            confidence=0.95 if ok else 0.0,
            checks=["provider_identity", "current_conditions_shape", "daily_forecast_shape"],
        )
