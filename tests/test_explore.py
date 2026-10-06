"""Explore calendar API normalization and caching."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.api import routes_explore
from backend.services import rss_poster


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
                "poster_url": "/api/rss/bangumi/42/poster", "rating": 8.2,
                "air_date": "2026-09-29",
            }]}])
            client.get("/api/explore/calendar")
            fetch.assert_awaited_once()

    def test_calendar_handles_smaller_and_missing_covers(self):
        source = [{"weekday": {"id": 1}, "items": [
            {"id": 10, "type": 2, "images": {"small": "https://lain.bgm.tv/small.jpg"}},
            {"id": 11, "type": 2, "images": None},
        ]}]
        with patch.object(routes_explore.bangumi, "get_calendar", new_callable=AsyncMock, return_value=source):
            items = TestClient(app).get("/api/explore/calendar").json()[0]["items"]
        self.assertEqual(items[0]["poster_url"], "/api/rss/bangumi/10/poster")
        self.assertEqual(items[1]["poster_url"], "")

    def test_calendar_cover_url_serves_cached_image(self):
        source = [{"weekday": {"id": 1}, "items": [
            {"id": 42, "type": 2, "images": {"large": "https://lain.bgm.tv/cover.jpg"}},
        ]}]
        with TemporaryDirectory() as directory:
            poster = Path(directory) / "rss_posters" / "42.jpg"
            poster.parent.mkdir()
            poster.write_bytes(b"cached-image")
            with (
                patch.object(routes_explore.bangumi, "get_calendar", new_callable=AsyncMock, return_value=source),
                patch.object(rss_poster.data, "_USER_DATA_DIR", Path(directory)),
                patch.object(rss_poster, "get_subject", new_callable=AsyncMock) as subject,
            ):
                client = TestClient(app)
                item = client.get("/api/explore/calendar").json()[0]["items"][0]
                response = client.get(item["poster_url"])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"cached-image")
                self.assertEqual(response.headers["content-type"], "image/jpeg")
                subject.assert_not_awaited()

    def test_cover_fetch_failure_is_logged(self):
        with TemporaryDirectory() as directory:
            with (
                patch.object(rss_poster.data, "_USER_DATA_DIR", Path(directory)),
                patch.object(rss_poster, "get_subject", new_callable=AsyncMock, side_effect=RuntimeError("upstream failed")),
                self.assertLogs(rss_poster.logger, level="WARNING") as logs,
            ):
                response = TestClient(app).get("/api/rss/bangumi/42/poster")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(any("id=42" in message and "RuntimeError" in message for message in logs.output))


if __name__ == "__main__":
    unittest.main()
