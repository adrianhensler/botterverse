from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
from typing import List, Protocol, Sequence

import httpx

from . import IntegrationEvent

NEWS_API_URL = "https://newsapi.org/v2/top-headlines"
NEWS_API_SEARCH_URL = "https://newsapi.org/v2/everything"
DEFAULT_HEADLINE_TITLE = "News headline"
_QUERY_PATTERN = re.compile(r"^[\w\s,.'\"-:?!&/()]{2,}$", re.UNICODE)


@dataclass(frozen=True)
class NewsHeadline:
    title: str
    url: str | None
    source: str | None
    published_at: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at,
        }


class NewsProvider(Protocol):
    name: str

    def search(self, query: str, limit: int, timeout_s: float) -> Sequence[NewsHeadline]:
        raise NotImplementedError


class NewsApiProvider:
    name = "newsapi"

    def __init__(self, api_key: str, base_url: str = NEWS_API_SEARCH_URL) -> None:
        self._api_key = api_key
        self._base_url = base_url

    def search(self, query: str, limit: int, timeout_s: float) -> Sequence[NewsHeadline]:
        if not self._api_key:
            raise ValueError("news API key not configured")
        params = {
            "apiKey": self._api_key,
            "q": query,
            "pageSize": limit,
            "sortBy": "publishedAt",
            "language": "en",
        }
        try:
            response = httpx.get(self._base_url, params=params, timeout=timeout_s)
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        payload = response.json()
        articles = payload.get("articles", [])
        results: list[NewsHeadline] = []
        for article in articles:
            title = article.get("title") or DEFAULT_HEADLINE_TITLE
            results.append(
                NewsHeadline(
                    title=title,
                    url=article.get("url"),
                    source=(article.get("source") or {}).get("name"),
                    published_at=article.get("publishedAt"),
                )
            )
        return results


def _normalize_limit(limit: int, min_value: int = 1, max_value: int = 5) -> int:
    return max(min_value, min(int(limit), max_value))


def validate_news_query(query: str) -> str:
    cleaned = query.strip()
    if not cleaned:
        raise ValueError("query is required")
    if len(cleaned) > 200:
        raise ValueError("query is too long")
    if not _QUERY_PATTERN.match(cleaned):
        raise ValueError("query contains invalid characters")
    return cleaned


def get_news_provider(provider_name: str | None = None, api_key: str | None = None) -> NewsProvider:
    provider = (provider_name or os.getenv("NEWS_PROVIDER", "newsapi")).strip().lower()
    if provider == "newsapi":
        return NewsApiProvider(api_key or os.getenv("NEWS_API_KEY", ""))
    raise ValueError(f"unsupported news provider: {provider}")


def search_news(
    query: str,
    *,
    limit: int = 3,
    timeout_s: float = 10.0,
    provider: NewsProvider | None = None,
    provider_name: str | None = None,
    api_key: str | None = None,
) -> list[dict[str, str | None]]:
    cleaned_query = validate_news_query(query)
    normalized_limit = _normalize_limit(limit)
    chosen_provider = provider or get_news_provider(provider_name, api_key=api_key)
    results = chosen_provider.search(cleaned_query, normalized_limit, timeout_s)
    return [headline.as_dict() for headline in results]


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
