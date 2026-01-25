import os
import unittest
from unittest.mock import Mock, patch

import httpx

from app.integrations.news import (
    DEFAULT_HEADLINE_TITLE,
    NEWS_API_SEARCH_URL,
    NewsApiProvider,
    TavilyProvider,
    get_news_provider,
    search_news,
)


class NewsSearchTest(unittest.TestCase):
    def test_search_news_normalizes_results(self) -> None:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "articles": [
                {
                    "title": "Example headline",
                    "url": "https://example.com/story",
                    "source": {"name": "Example Source"},
                    "publishedAt": "2024-01-01T00:00:00Z",
                },
                {
                    "title": None,
                    "url": None,
                    "source": {},
                    "publishedAt": None,
                },
            ]
        }
        provider = NewsApiProvider(api_key="test-key", base_url=NEWS_API_SEARCH_URL)
        with patch("app.integrations.news.httpx.get", return_value=mock_response) as mocked_get:
            results = search_news(
                "OpenAI",
                limit=10,
                timeout_s=5.0,
                provider=provider,
            )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Example headline")
        self.assertEqual(results[0]["url"], "https://example.com/story")
        self.assertEqual(results[0]["source"], "Example Source")
        self.assertEqual(results[0]["published_at"], "2024-01-01T00:00:00Z")
        self.assertEqual(results[1]["title"], DEFAULT_HEADLINE_TITLE)
        mocked_get.assert_called_once_with(
            NEWS_API_SEARCH_URL,
            params={
                "apiKey": "test-key",
                "q": "OpenAI",
                "pageSize": 5,
                "sortBy": "publishedAt",
                "language": "en",
            },
            timeout=5.0,
        )

    def test_search_news_propagates_http_error(self) -> None:
        provider = NewsApiProvider(api_key="test-key", base_url=NEWS_API_SEARCH_URL)
        with patch(
            "app.integrations.news.httpx.get",
            side_effect=httpx.HTTPError("boom"),
        ):
            with self.assertRaises(httpx.HTTPError):
                search_news("OpenAI", provider=provider)


class TavilyProviderTest(unittest.TestCase):
    def test_tavily_search_success(self):
        """Test successful Tavily search"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "results": [
                {
                    "title": "Breaking News",
                    "url": "https://example.com/article",
                    "content": "Article snippet...",
                    "score": 0.95,
                    "published_date": "2024-01-20",
                }
            ]
        }

        with patch("app.integrations.news.httpx.post", return_value=mock_response):
            provider = TavilyProvider("test-key")
            results = provider.search("test query", limit=3, timeout_s=10.0)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Breaking News")
        self.assertEqual(results[0].url, "https://example.com/article")
        self.assertEqual(results[0].source, "example.com")
        self.assertEqual(results[0].published_at, "2024-01-20")

    def test_tavily_handles_missing_published_date(self):
        """Test Tavily results without published_date"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "results": [
                {
                    "title": "Web Article",
                    "url": "https://blog.example.com/post",
                    "content": "Content...",
                    # No published_date field
                }
            ]
        }

        with patch("app.integrations.news.httpx.post", return_value=mock_response):
            provider = TavilyProvider("test-key")
            results = provider.search("test", limit=1, timeout_s=10.0)

        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0].published_at)

    def test_tavily_api_error_handling(self):
        """Test Tavily API error handling"""
        with patch("app.integrations.news.httpx.post", side_effect=httpx.HTTPError("Network error")):
            provider = TavilyProvider("test-key")

            with self.assertRaises(ValueError):
                provider.search("test", limit=1, timeout_s=10.0)

    def test_tavily_missing_api_key(self):
        """Test Tavily with missing API key"""
        provider = TavilyProvider("")

        with self.assertRaises(ValueError) as context:
            provider.search("test", limit=1, timeout_s=10.0)

        self.assertIn("Tavily API key not configured", str(context.exception))

    def test_get_news_provider_tavily(self):
        """Test provider factory returns Tavily"""
        with patch.dict(os.environ, {"NEWS_PROVIDER": "tavily", "TAVILY_API_KEY": "test-key"}):
            provider = get_news_provider()

            self.assertEqual(provider.name, "tavily")

    def test_tavily_search_depth_configuration(self):
        """Test Tavily search depth can be configured"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"results": []}

        with patch("app.integrations.news.httpx.post", return_value=mock_response) as mocked_post:
            provider = TavilyProvider("test-key", search_depth="advanced")
            provider.search("test", limit=3, timeout_s=10.0)

            # Verify the request payload includes search_depth
            call_args = mocked_post.call_args
            payload = call_args[1]["json"]
            self.assertEqual(payload["search_depth"], "advanced")


if __name__ == "__main__":
    unittest.main()
