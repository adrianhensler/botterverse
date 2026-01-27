from __future__ import annotations

from datetime import datetime, timezone
import os
import re
from typing import List, Mapping

import httpx

from . import IntegrationEvent

WEATHER_API_URL = "https://api.openweathermap.org/data/2.5/weather"
WEATHER_FORECAST_API_URL = "https://api.openweathermap.org/data/2.5/forecast"
VALID_UNITS = {"metric", "imperial", "standard"}
_LOCATION_PATTERN = re.compile(r"^[\w\s,.'-]{2,}$", re.UNICODE)

# In-memory cache: {normalized_location: (weather_data, timestamp)}
_WEATHER_CACHE: dict[str, tuple[dict, datetime]] = {}
WEATHER_CACHE_TTL_MINUTES = int(os.getenv("WEATHER_CACHE_TTL_MINUTES", "15"))


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


def _get_cached_weather(location: str) -> Mapping[str, object] | None:
    """Get cached weather if still valid (within TTL)."""
    if location not in _WEATHER_CACHE:
        return None

    weather_data, cached_at = _WEATHER_CACHE[location]
    age_minutes = (datetime.now(timezone.utc) - cached_at).total_seconds() / 60

    if age_minutes < WEATHER_CACHE_TTL_MINUTES:
        # Cache hit - return with updated timestamp
        return {**weather_data, "cached": True, "cache_age_minutes": round(age_minutes, 1)}

    # Cache expired - remove it
    del _WEATHER_CACHE[location]
    return None


def _cache_weather(location: str, weather_data: dict) -> None:
    """Cache weather data for a location."""
    _WEATHER_CACHE[location] = (weather_data, datetime.now(timezone.utc))

    # Cleanup: remove stale entries (older than 2x TTL)
    max_age_minutes = WEATHER_CACHE_TTL_MINUTES * 2
    now = datetime.now(timezone.utc)
    stale_keys = [
        key for key, (_, timestamp) in _WEATHER_CACHE.items()
        if (now - timestamp).total_seconds() / 60 > max_age_minutes
    ]
    for key in stale_keys:
        del _WEATHER_CACHE[key]


def normalize_location_format(location: str) -> list[str]:
    """
    Convert natural language location to API-compatible formats.

    Returns list of format attempts in priority order.
    Examples:
        "Halifax NS" → ["Halifax,NS,CA", "Halifax,CA", "Halifax"]
        "Toronto" → ["Toronto,CA", "Toronto,ON,CA", "Toronto"]
        "New York" → ["New York,US", "New York,NY,US", "New York"]
    """
    # Common state/province mappings (North America focus)
    state_codes = {
        # Canada provinces
        "NS": ("NS", "CA"), "AB": ("AB", "CA"), "BC": ("BC", "CA"),
        "MB": ("MB", "CA"), "NB": ("NB", "CA"), "NL": ("NL", "CA"),
        "ON": ("ON", "CA"), "PE": ("PE", "CA"), "QC": ("QC", "CA"),
        "SK": ("SK", "CA"), "NT": ("NT", "CA"), "NU": ("NU", "CA"),
        "YT": ("YT", "CA"),
        # US states (common ones)
        "AL": ("AL", "US"), "AK": ("AK", "US"), "AZ": ("AZ", "US"),
        "AR": ("AR", "US"), "CA": ("CA", "US"), "CO": ("CO", "US"),
        "CT": ("CT", "US"), "DE": ("DE", "US"), "FL": ("FL", "US"),
        "GA": ("GA", "US"), "HI": ("HI", "US"), "ID": ("ID", "US"),
        "IL": ("IL", "US"), "IN": ("IN", "US"), "IA": ("IA", "US"),
        "KS": ("KS", "US"), "KY": ("KY", "US"), "LA": ("LA", "US"),
        "ME": ("ME", "US"), "MD": ("MD", "US"), "MA": ("MA", "US"),
        "MI": ("MI", "US"), "MN": ("MN", "US"), "MS": ("MS", "US"),
        "MO": ("MO", "US"), "MT": ("MT", "US"), "NE": ("NE", "US"),
        "NV": ("NV", "US"), "NH": ("NH", "US"), "NJ": ("NJ", "US"),
        "NM": ("NM", "US"), "NY": ("NY", "US"), "NC": ("NC", "US"),
        "ND": ("ND", "US"), "OH": ("OH", "US"), "OK": ("OK", "US"),
        "OR": ("OR", "US"), "PA": ("PA", "US"), "RI": ("RI", "US"),
        "SC": ("SC", "US"), "SD": ("SD", "US"), "TN": ("TN", "US"),
        "TX": ("TX", "US"), "UT": ("UT", "US"), "VT": ("VT", "US"),
        "VA": ("VA", "US"), "WA": ("WA", "US"), "WV": ("WV", "US"),
        "WI": ("WI", "US"), "WY": ("WY", "US"), "DC": ("DC", "US"),
    }

    # Common city → country defaults (for major cities)
    city_defaults = {
        "toronto": "CA", "vancouver": "CA", "montreal": "CA",
        "calgary": "CA", "ottawa": "CA", "edmonton": "CA",
        "london": "UK", "paris": "FR", "berlin": "DE",
        "tokyo": "JP", "sydney": "AU", "melbourne": "AU",
    }

    location = location.strip()
    formats = []

    # Check if already in API format (contains comma)
    if "," in location:
        formats.append(location)
        return formats

    # Try to extract state/province code
    # Pattern: "City ST" or "City State"
    parts = location.split()
    city_name = location
    if len(parts) >= 2:
        potential_city = " ".join(parts[:-1])
        potential_code = parts[-1].upper()

        if potential_code in state_codes:
            state, country = state_codes[potential_code]
            formats.append(f"{potential_city},{state},{country}")
            formats.append(f"{potential_city},{country}")
            city_name = potential_city  # Store the city part for fallback

    # Try city with default country
    city_lower = location.split()[0].lower()
    if city_lower in city_defaults:
        country = city_defaults[city_lower]
        # Avoid duplicate if we already added it with state code
        candidate = f"{location},{country}"
        if candidate not in formats:
            formats.append(candidate)

    # Fallback: try just the city name without state code
    if city_name not in formats:
        formats.append(city_name)

    return formats


def fetch_weather_with_retry(
    api_key: str,
    location: str,
    units: str = "metric",
    timeout_s: float = 10.0,
) -> tuple[Mapping[str, object] | None, list[str], str | None]:
    """
    Fetch weather with automatic retry for different location formats.
    Uses per-location caching with 15-minute TTL.

    Returns:
        (weather_data, attempted_formats, error_type) tuple
        weather_data is None if all attempts failed
        error_type is None on success, or one of:
            "not_found" - Location not found (404)
            "auth_error" - Invalid API key (401)
            "rate_limited" - Rate limit exceeded (429)
            "unavailable" - Network error or other failure
    """
    if not api_key:
        return (None, [], "auth_error")

    location_formats = normalize_location_format(location)
    attempted = []
    last_error_type = None

    for fmt in location_formats:
        attempted.append(fmt)
        try:
            cleaned = validate_weather_location(fmt)
        except ValueError:
            continue

        # Check cache first (keyed by cleaned format + units)
        cache_key = f"{cleaned}:{units}"
        cached = _get_cached_weather(cache_key)
        if cached:
            return (cached, attempted, None)

        normalized_units = normalize_weather_units(units)
        params = {
            "q": cleaned,
            "appid": api_key,
            "units": normalized_units,
        }

        try:
            response = httpx.get(WEATHER_API_URL, params=params, timeout=timeout_s)
            response.raise_for_status()

            # Success! Parse and return
            data = response.json()
            weather = (data.get("weather") or [{}])[0]
            main = data.get("main", {})
            wind = data.get("wind", {})
            summary = weather.get("description") or "weather update"

            weather_data = {
                "location": cleaned,
                "summary": summary,
                "temperature": main.get("temp"),
                "feels_like": main.get("feels_like"),
                "humidity": main.get("humidity"),
                "wind_speed": wind.get("speed"),
                "observation_time": data.get("dt"),
                "units": normalized_units,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "cached": False,
            }

            # Cache the result
            _cache_weather(cache_key, weather_data)

            return (weather_data, attempted, None)

        except httpx.HTTPStatusError as e:
            # Classify error by status code
            if e.response.status_code == 404:
                last_error_type = "not_found"
            elif e.response.status_code == 401:
                last_error_type = "auth_error"
                break  # Don't retry location formats for auth errors
            elif e.response.status_code == 429:
                last_error_type = "rate_limited"
                break  # Don't retry location formats for rate limits
            else:
                last_error_type = "unavailable"
            continue  # Try next format for 404s and 5xx errors

        except httpx.HTTPError:
            # Network errors, timeouts, etc.
            last_error_type = "unavailable"
            continue

    # All attempts failed
    return (None, attempted, last_error_type or "unavailable")


def fetch_weather(
    api_key: str,
    location: str,
    units: str = "metric",
    timeout_s: float = 10.0,
) -> Mapping[str, object] | None:
    """Fetch current weather with automatic format retry."""
    weather_data, _attempted, _error_type = fetch_weather_with_retry(
        api_key, location, units, timeout_s
    )
    return weather_data


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


def fetch_weather_forecast(
    api_key: str,
    location: str,
    units: str = "metric",
    timeout_s: float = 10.0,
) -> Mapping[str, object] | None:
    cleaned = validate_weather_location(location)
    params = {
        "q": cleaned,
        "appid": api_key,
        "units": normalize_weather_units(units),
    }
    response = httpx.get(WEATHER_FORECAST_API_URL, params=params, timeout=timeout_s)
    response.raise_for_status()
    data = response.json()
    entries = data.get("list") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    daily_buckets: dict[str, dict[str, object]] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        dt = item.get("dt")
        if not isinstance(dt, (int, float)):
            continue
        date_key = datetime.fromtimestamp(float(dt), tz=timezone.utc).strftime("%Y-%m-%d")
        main = item.get("main") if isinstance(item.get("main"), dict) else {}
        weather = (item.get("weather") or [{}])[0]
        temp_min = main.get("temp_min")
        temp_max = main.get("temp_max")
        humidity = main.get("humidity")
        wind = item.get("wind") if isinstance(item.get("wind"), dict) else {}
        wind_speed = wind.get("speed")
        bucket = daily_buckets.setdefault(
            date_key,
            {
                "date": dt,
                "summary": weather.get("description"),
                "temp_min": temp_min,
                "temp_max": temp_max,
                "humidity": humidity,
                "wind_speed": wind_speed,
            },
        )
        if temp_min is not None and bucket.get("temp_min") is not None:
            bucket["temp_min"] = min(float(bucket["temp_min"]), float(temp_min))
        elif temp_min is not None:
            bucket["temp_min"] = temp_min
        if temp_max is not None and bucket.get("temp_max") is not None:
            bucket["temp_max"] = max(float(bucket["temp_max"]), float(temp_max))
        elif temp_max is not None:
            bucket["temp_max"] = temp_max
        if bucket.get("summary") is None and weather.get("description"):
            bucket["summary"] = weather.get("description")

    parsed_daily = list(daily_buckets.values())[:5]
    return {
        "location": cleaned,
        "timezone": (data.get("city") or {}).get("timezone") if isinstance(data.get("city"), dict) else None,
        "daily": parsed_daily,
        "units": normalize_weather_units(units),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
