from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import httpx

from . import IntegrationEvent

SPORTS_API_BASE = "https://www.thesportsdb.com/api/v1/json"


def fetch_sports_events(api_key: str, league_id: str, limit: int = 3) -> List[IntegrationEvent]:
    if not api_key or not league_id:
        return []
    url = f"{SPORTS_API_BASE}/{api_key}/eventsnextleague.php"
    params = {"id": league_id}
    try:
        response = httpx.get(url, params=params, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError:
        return []
    data = response.json()
    events = data.get("events") or []
    integration_events: List[IntegrationEvent] = []
    for event in events[:limit]:
        home = event.get("strHomeTeam")
        away = event.get("strAwayTeam")
        league = event.get("strLeague")
        event_id = event.get("idEvent")
        kickoff = event.get("dateEvent")
        topic = f"Sports: {away} at {home} ({league})"
        integration_events.append(
            IntegrationEvent(
                kind="sports",
                topic=topic,
                external_id=f"sports:{event_id}",
                payload={
                    "home_team": home,
                    "away_team": away,
                    "league": league,
                    "event_id": event_id,
                    "kickoff_date": kickoff,
                    "venue": event.get("strVenue"),
                    "status": event.get("strStatus"),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
    return integration_events
