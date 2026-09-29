"""Explore calendar API normalization and caching."""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.api import routes_explore


class ExploreCalendarTests(unittest.TestCase):
    def setUp(self):
        routes_explore._cache = None
        routes_explore._cache_until = 0

    def test_calendar_maps_subjects_and_caches_result(self):
        source = [{
            "weekday": {"id": 2, "cn": "星期二"},
            "items": [
                {"id": 42, "type": 2, "name": "Original", "name_cn": "中文名",
                 "images": {"large": "https://example.com/poster.jpg"},
                 "rating": {"score": 8.2}, "air_date": "2026-09-29"},
                {"id": 43, "type": 1, "name": "Book"},
            ],
        }]
        with patch.object(routes_explore.bangumi, "get_calendar", new_callable=AsyncMock, return_value=source) as fetch:
            client = TestClient(app)
            response = client.get("/api/explore/calendar")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), [{"weekday": 2, "items": [{
                "id": 42, "name": "中文名", "original_name": "Original",
                "poster_url": "https://example.com/poster.jpg", "rating": 8.2,
                "air_date": "2026-09-29",
            }]}])
            client.get("/api/explore/calendar")
            fetch.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
