"""RSS timestamps must be compared as dates, including shifted Bangumi sorts."""

import unittest
from unittest.mock import AsyncMock, patch

from backend.services import downloader, enrich
from backend.utils.rss_dates import publication_datetime, published_before_air_date


class RssDateTests(unittest.IsolatedAsyncioTestCase):
    def test_rfc822_dates_are_parsed(self):
        self.assertTrue(published_before_air_date("Thu, 24 Sep 2026 23:00:00 +0000", "2026-09-25"))
        self.assertFalse(published_before_air_date("Fri, 25 Sep 2026 00:00:00 +0000", "2026-09-25"))
        self.assertLess(publication_datetime("Fri, 25 Sep 2026 00:00:00 +0000"),
                        publication_datetime("Sat, 26 Sep 2026 00:00:00 +0000"))

    async def test_offset_ignores_pre_premiere_episode(self):
        feed = {"items": [
            {"episode_number": 1, "pub_date": "Thu, 24 Sep 2026 00:00:00 +0000"},
            {"episode_number": 2, "pub_date": "Fri, 25 Sep 2026 00:00:00 +0000"},
        ]}
        self.assertEqual(enrich._compute_rss_offset(feed, "2026-09-25"), 2)

    async def test_second_episode_starts_bangumi_range(self):
        feed = {"items": [
            {"episode_number": 3, "pub_date": "Sat, 26 Sep 2026 00:00:00 +0000",
             "passed": True, "excluded": False},
            {"episode_number": 1, "pub_date": "Thu, 24 Sep 2026 00:00:00 +0000",
             "passed": True, "excluded": False},
            {"episode_number": 2, "pub_date": "Fri, 25 Sep 2026 00:00:00 +0000",
             "passed": True, "excluded": False},
        ]}
        with patch.object(downloader.rss_service, "fetch_rss_snapshot", new_callable=AsyncMock,
                          return_value=feed), \
             patch.object(downloader, "get_all_episodes", return_value={}), \
             patch.object(downloader, "get_fail_count", return_value=0), \
             patch.object(downloader, "get_episode_source", return_value=None):
            items = await downloader._fetch_passed_items(
                "https://feed", [], 639938, bgm_sortrange=[2, 12],
                air_date="2026-09-25", rss_offset=0)
        self.assertEqual([item["sort"] for item in items], [2, 3])

    async def test_excluded_items_log_reasons(self):
        feed = {"items": [
            {"title": "old", "tags": ["1080p"], "episode_number": 1, "pub_date": "Thu, 24 Sep 2026 00:00:00 +0000",
             "passed": True, "excluded": False},
            {"title": "out of range", "tags": ["1080p"], "episode_number": 13,
             "pub_date": "Sat, 26 Sep 2026 00:00:00 +0000",
             "passed": True, "excluded": False},
            {"title": "wrong tag", "episode_number": 2, "tags": ["720p"],
             "pub_date": "Fri, 25 Sep 2026 00:00:00 +0000",
             "passed": False, "excluded": False},
        ]}
        with patch.object(downloader.rss_service, "fetch_rss_snapshot", new_callable=AsyncMock,
                          return_value=feed), \
             patch.object(downloader, "get_all_episodes", return_value={}), \
             self.assertLogs(downloader.logger, level="INFO") as logs:
            items = await downloader._fetch_passed_items(
                "https://feed", ["1080p"], 639938, bgm_sortrange=[2, 12],
                air_date="2026-09-25", rss_offset=0)
        self.assertEqual(items, [])
        output = "\n".join(logs.output)
        self.assertIn("早于首播日期", output)
        self.assertIn("不在 Bangumi 范围", output)
        self.assertIn("标签不匹配", output)
        self.assertIn("candidates=0 excluded=3", output)


if __name__ == "__main__":
    unittest.main()
