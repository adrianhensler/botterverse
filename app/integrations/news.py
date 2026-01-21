from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

import httpx

from . import IntegrationEvent

NEWS_API_URL = "https://newsapi.org/v2/top-headlines"


def fetch_news_events(api_key: str, country: str = "us", limit: int = 3) -> List[IntegrationEvent]:
    if not api_key:
        return []
    params = {
        "apiKey": api_key,
        "country": country,
        "pageSize": limit,
    }
    try:
        response = httpx.get(NEWS_API_URL, params=params, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError:
        return []
    payload = response.json()
    articles = payload.get("articles", [])
    events: List[IntegrationEvent] = []
    for article in articles:
        title = article.get("title") or "Top headline"
        source = (article.get("source") or {}).get("name")
        url = article.get("url")
        published_at = article.get("publishedAt")
        topic = f"News: {title}"
        external_id = url or f"news:{title}:{published_at}"
        events.append(
            IntegrationEvent(
                kind="news",
                topic=topic,
                external_id=external_id,
                payload={
                    "title": title,
                    "source": source,
                    "url": url,
                    "summary": article.get("description"),
                    "published_at": published_at,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
    return events
