# Weather integration

MATRIX can request current conditions and a bounded 1–7 day forecast from Open-Meteo. This endpoint does not require an API key and is read-only.

## Tool

`weather_forecast` accepts latitude, longitude, forecast-day count and timezone. Coordinates are range-validated, requests have a bounded timeout, and response arrays are checked for completeness before results are returned.

Permission: `weather.read` (enabled by default). Risk: low. No write actions are available.

Example tool arguments:

```json
{"latitude":28.98,"longitude":77.71,"forecast_days":3,"timezone":"Asia/Kolkata"}
```

The current tool expects coordinates. It does not infer a precise location or silently access device location; provide a place's coordinates when requesting a forecast. Open-Meteo usage limits and provider availability still apply.
