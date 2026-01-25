import unittest
from unittest.mock import Mock, patch

import httpx

from app.integrations.news import DEFAULT_HEADLINE_TITLE, NEWS_API_SEARCH_URL, NewsApiProvider, search_news


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

    def test_search_news_handles_http_error(self) -> None:
        provider = NewsApiProvider(api_key="test-key", base_url=NEWS_API_SEARCH_URL)
        with patch(
            "app.integrations.news.httpx.get",
            side_effect=httpx.HTTPError("boom"),
        ):
            results = search_news("OpenAI", provider=provider)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
