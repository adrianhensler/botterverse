from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import httpx

from . import IntegrationEvent

WEATHER_API_URL = "https://api.openweathermap.org/data/2.5/weather"


def fetch_weather_events(api_key: str, location: str, units: str = "metric") -> List[IntegrationEvent]:
    if not api_key or not location:
        return []
    params = {
        "q": location,
        "appid": api_key,
        "units": units,
    }
    try:
        response = httpx.get(WEATHER_API_URL, params=params, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError:
        return []
    data = response.json()
    weather = (data.get("weather") or [{}])[0]
    main = data.get("main", {})
    wind = data.get("wind", {})
    summary = weather.get("description") or "weather update"
    topic = f"Weather: {summary} in {location}"
    external_id = f"weather:{location}:{data.get('dt')}"
    return [
        IntegrationEvent(
            kind="weather",
            topic=topic,
            external_id=external_id,
            payload={
                "location": location,
                "summary": summary,
                "temperature": main.get("temp"),
                "feels_like": main.get("feels_like"),
                "humidity": main.get("humidity"),
                "wind_speed": wind.get("speed"),
                "observation_time": data.get("dt"),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    ]
