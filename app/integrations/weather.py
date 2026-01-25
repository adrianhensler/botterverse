from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import List, Mapping

import httpx

from . import IntegrationEvent

WEATHER_API_URL = "https://api.openweathermap.org/data/2.5/weather"
VALID_UNITS = {"metric", "imperial", "standard"}
_LOCATION_PATTERN = re.compile(r"^[\w\s,.'-]{2,}$", re.UNICODE)


def normalize_weather_units(units: str | None) -> str:
    if not units:
        return "metric"
    normalized = units.strip().lower()
    if normalized in VALID_UNITS:
        return normalized
    return "metric"


def validate_weather_location(location: str) -> str:
    cleaned = location.strip()
    if not cleaned:
        raise ValueError("location is required")
    if len(cleaned) > 100:
        raise ValueError("location is too long")
    if not _LOCATION_PATTERN.match(cleaned):
        raise ValueError("location contains invalid characters")
    return cleaned


def fetch_weather(
    api_key: str,
    location: str,
    units: str = "metric",
    timeout_s: float = 10.0,
) -> Mapping[str, object] | None:
    if not api_key:
        return None
    try:
        cleaned_location = validate_weather_location(location)
    except ValueError:
        return None
    normalized_units = normalize_weather_units(units)
    params = {
        "q": cleaned_location,
        "appid": api_key,
        "units": normalized_units,
    }
    try:
        response = httpx.get(WEATHER_API_URL, params=params, timeout=timeout_s)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    data = response.json()
    weather = (data.get("weather") or [{}])[0]
    main = data.get("main", {})
    wind = data.get("wind", {})
    summary = weather.get("description") or "weather update"
    return {
        "location": cleaned_location,
        "summary": summary,
        "temperature": main.get("temp"),
        "feels_like": main.get("feels_like"),
        "humidity": main.get("humidity"),
        "wind_speed": wind.get("speed"),
        "observation_time": data.get("dt"),
        "units": normalized_units,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_weather_events(api_key: str, location: str, units: str = "metric") -> List[IntegrationEvent]:
    normalized = fetch_weather(api_key, location, units=units, timeout_s=10.0)
    if not normalized:
        return []
    summary = str(normalized.get("summary", "weather update"))
    location_name = str(normalized.get("location", location))
    topic = f"Weather: {summary} in {location_name}"
    external_id = f"weather:{location_name}:{normalized.get('observation_time')}"
    return [
        IntegrationEvent(
            kind="weather",
            topic=topic,
            external_id=external_id,
            payload={
                **normalized,
            },
        )
    ]
