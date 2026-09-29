"""The first RSS poll must target only the newly saved subscription."""

import unittest
from unittest.mock import AsyncMock, patch

from backend.services import downloader, initial_rss_poll


class InitialRssPollTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await initial_rss_poll.stop()
        initial_rss_poll._jobs.clear()

    async def test_single_poll_only_processes_requested_subscription(self):
        subs = [{"bangumi_id": 1, "active": 1}, {"bangumi_id": 2, "active": 1}]
        with patch.object(downloader, "list_subscriptions", return_value=subs), patch.object(
            downloader, "_process_subscription", new_callable=AsyncMock, return_value=3,
        ) as process:
            result = await downloader.poll_subscription(2)
        process.assert_awaited_once_with(subs[1], strict=True)
        self.assertEqual(result["downloaded"], 3)

    async def test_enrichment_is_saved_before_single_poll(self):
        sub = {"bangumi_id": 12, "primary": {"rss_url": "https://feed"}, "backup": {}}
        result = {"bgm": {"season": 1}, "tmdb": {"id": 4},
                  "primary_offset": 0, "backup_offset": None}
        events = []

        def save(*args):
            events.append("save")
            return True

        async def poll(*args):
            events.append("poll")
            return {"state": "completed", "message": "首次轮询完成", "downloaded": 2}

        with patch.object(initial_rss_poll.data, "list_subscriptions", return_value=[sub]), \
             patch.object(initial_rss_poll.data, "update_subscription", side_effect=save), \
             patch.object(initial_rss_poll.data, "set_subscription_rss_offset", side_effect=save), \
             patch.object(initial_rss_poll.downloader, "enrich_subscription", new_callable=AsyncMock, return_value=result), \
             patch.object(initial_rss_poll.downloader, "poll_subscription", side_effect=poll):
            initial_rss_poll.start(12)
            await initial_rss_poll._tasks[12]
        self.assertEqual(events, ["save", "save", "poll"])
        self.assertEqual(initial_rss_poll.get_status(12)["downloaded"], 2)

    async def test_missing_offset_skips_download(self):
        sub = {"bangumi_id": 12, "primary": {"rss_url": "https://feed"}, "backup": {}}
        result = {"bgm": {"season": 1}, "tmdb": {"id": 4},
                  "primary_offset": None, "backup_offset": None}
        with patch.object(initial_rss_poll.data, "list_subscriptions", return_value=[sub]), \
             patch.object(initial_rss_poll.data, "update_subscription", return_value=True), \
             patch.object(initial_rss_poll.downloader, "enrich_subscription", new_callable=AsyncMock, return_value=result), \
             patch.object(initial_rss_poll.downloader, "poll_subscription", new_callable=AsyncMock) as poll:
            initial_rss_poll.start(12)
            await initial_rss_poll._tasks[12]
        poll.assert_not_awaited()
        self.assertEqual(initial_rss_poll.get_status(12)["state"], "skipped")

    async def test_stale_poll_snapshot_uses_saved_zero_offset(self):
        stale = {"bangumi_id": 12, "active": 1, "primary": {"rss_url": "https://feed"}}
        current = {"bangumi_id": 12, "active": 1,
                   "bgm": {"sortrange": [2, 12], "air_date": "2026-09-25"},
                   "primary": {"rss_url": "https://feed", "offset": 0}, "backup": {}}
        with patch.object(downloader, "list_subscriptions", return_value=[current]), \
             patch.object(downloader, "_fetch_passed_items", new_callable=AsyncMock,
                          return_value=[]) as fetch, \
             patch.object(downloader, "_compute_rss_offset", new_callable=AsyncMock) as compute:
            self.assertEqual(await downloader._process_subscription(stale), 0)
        self.assertEqual(fetch.await_args.kwargs["rss_offset"], 0)
        compute.assert_not_awaited()

    async def test_missing_zero_offset_is_recomputed_and_saved(self):
        sub = {"bangumi_id": 12, "active": 1,
               "bgm": {"sortrange": [2, 12], "air_date": "2026-09-25"},
               "primary": {"rss_url": "https://feed"}, "backup": {}}
        with patch.object(downloader, "list_subscriptions", return_value=[sub]), \
             patch.object(downloader, "_compute_rss_offset", new_callable=AsyncMock,
                          return_value=2), \
             patch("backend.data.set_subscription_rss_offset", return_value=True) as save, \
             patch.object(downloader, "_fetch_passed_items", new_callable=AsyncMock,
                          return_value=[]) as fetch:
            self.assertEqual(await downloader._process_subscription(sub), 0)
        save.assert_called_once_with(12, "primary", 0)
        self.assertEqual(fetch.await_args.kwargs["rss_offset"], 0)


if __name__ == "__main__":
    unittest.main()
