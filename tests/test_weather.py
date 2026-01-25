import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone

import httpx

from app.integrations.weather import (
    fetch_weather_with_retry,
    normalize_location_format,
    validate_weather_location,
)


class WeatherLocationTest(unittest.TestCase):
    def setUp(self):
        """Clear cache before each test to prevent interference"""
        from app.integrations.weather import _WEATHER_CACHE
        _WEATHER_CACHE.clear()

    def test_normalize_canadian_province(self):
        """Test 'Halifax NS' → ['Halifax,NS,CA', 'Halifax,CA', 'Halifax']"""
        formats = normalize_location_format("Halifax NS")
        self.assertIn("Halifax,NS,CA", formats)
        self.assertIn("Halifax,CA", formats)
        self.assertIn("Halifax", formats)

    def test_normalize_us_state(self):
        """Test 'New York NY' → ['New York,NY,US', 'New York,US', 'New York']"""
        formats = normalize_location_format("New York NY")
        self.assertIn("New York,NY,US", formats)
        self.assertIn("New York,US", formats)
        self.assertIn("New York", formats)

    def test_normalize_major_city(self):
        """Test 'Toronto' → ['Toronto,CA', 'Toronto']"""
        formats = normalize_location_format("Toronto")
        self.assertIn("Toronto,CA", formats)
        self.assertIn("Toronto", formats)

    def test_normalize_already_formatted(self):
        """Test 'Halifax,CA' → ['Halifax,CA'] (no change)"""
        formats = normalize_location_format("Halifax,CA")
        self.assertEqual(formats[0], "Halifax,CA")

    def test_validate_location_basic(self):
        """Test validation accepts valid formats"""
        self.assertEqual(validate_weather_location("Halifax NS"), "Halifax NS")
        self.assertEqual(validate_weather_location("Halifax,CA"), "Halifax,CA")

    def test_validate_location_rejects_empty(self):
        """Test validation rejects empty string"""
        with self.assertRaises(ValueError):
            validate_weather_location("")

    def test_validate_location_rejects_too_long(self):
        """Test validation rejects strings > 100 chars"""
        with self.assertRaises(ValueError):
            validate_weather_location("x" * 101)

    def test_fetch_with_retry_success_first_attempt(self):
        """Test successful fetch on first format attempt"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "weather": [{"description": "clear sky"}],
            "main": {"temp": 20, "feels_like": 18, "humidity": 50},
            "wind": {"speed": 5},
            "dt": 1234567890,
        }

        with patch("app.integrations.weather.httpx.get", return_value=mock_response):
            weather, attempted = fetch_weather_with_retry("test-key", "Halifax,CA")

        self.assertIsNotNone(weather)
        self.assertEqual(weather["temperature"], 20)
        self.assertEqual(weather["summary"], "clear sky")
        self.assertEqual(len(attempted), 1)

    def test_fetch_with_retry_success_second_attempt(self):
        """Test successful fetch on fallback format"""
        def mock_get(url, params, timeout):
            # First attempt fails, second succeeds
            if params["q"] == "Halifax,NS,CA":
                raise httpx.HTTPStatusError("404", request=Mock(), response=Mock())
            else:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.raise_for_status.return_value = None
                mock_response.json.return_value = {
                    "weather": [{"description": "cloudy"}],
                    "main": {"temp": 15, "feels_like": 13, "humidity": 60},
                    "wind": {"speed": 10},
                    "dt": 1234567890,
                }
                return mock_response

        with patch("app.integrations.weather.httpx.get", side_effect=mock_get):
            weather, attempted = fetch_weather_with_retry("test-key", "Halifax NS")

        self.assertIsNotNone(weather)
        self.assertEqual(weather["temperature"], 15)
        self.assertGreater(len(attempted), 1)

    def test_fetch_with_retry_all_fail(self):
        """Test all format attempts fail"""
        with patch("app.integrations.weather.httpx.get", side_effect=httpx.HTTPError("Network error")):
            weather, attempted = fetch_weather_with_retry("test-key", "InvalidCity123")

        self.assertIsNone(weather)
        self.assertGreater(len(attempted), 0)

    def test_cache_hit_returns_immediately(self):
        """Test cached weather returns without API call"""
        from app.integrations.weather import _WEATHER_CACHE, _cache_weather

        # Manually cache data
        cached_data = {
            "location": "Halifax,CA",
            "temperature": 20,
            "summary": "sunny",
            "units": "metric",
        }
        _cache_weather("Halifax,CA:metric", cached_data)

        # Fetch should return cached data without API call
        with patch("app.integrations.weather.httpx.get") as mock_get:
            weather, attempted = fetch_weather_with_retry("test-key", "Halifax,CA", units="metric")

        # No API call should have been made
        mock_get.assert_not_called()
        self.assertIsNotNone(weather)
        self.assertTrue(weather.get("cached"))
        self.assertEqual(weather.get("temperature"), 20)

    def test_cache_expiry(self):
        """Test expired cache entries are removed"""
        from app.integrations.weather import _WEATHER_CACHE, _get_cached_weather

        # Cache data with old timestamp (expired)
        old_time = datetime.now(timezone.utc) - timedelta(minutes=20)
        _WEATHER_CACHE["TestLocation:metric"] = ({"temp": 10}, old_time)

        # Should return None and remove from cache
        result = _get_cached_weather("TestLocation:metric")
        self.assertIsNone(result)
        self.assertNotIn("TestLocation:metric", _WEATHER_CACHE)

    def test_cache_cleanup_removes_stale(self):
        """Test cache cleanup removes old entries"""
        from app.integrations.weather import _WEATHER_CACHE, _cache_weather

        # Add stale entry (> 2x TTL)
        old_time = datetime.now(timezone.utc) - timedelta(minutes=35)
        _WEATHER_CACHE["Stale:metric"] = ({"temp": 5}, old_time)

        # Cache new entry (triggers cleanup)
        _cache_weather("Fresh:metric", {"temp": 20})

        # Stale entry should be removed
        self.assertNotIn("Stale:metric", _WEATHER_CACHE)
        self.assertIn("Fresh:metric", _WEATHER_CACHE)


if __name__ == "__main__":
    unittest.main()
