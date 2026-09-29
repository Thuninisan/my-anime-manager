"""The first RSS poll must target only the newly saved subscription."""

import unittest
from unittest.mock import AsyncMock, patch

from backend.services import downloader, initial_rss_poll


class InitialRssPollTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await initial_rss_poll.stop()
        initial_rss_poll._jobs.clear()

    async def test_subscription_refreshes_once_after_both_feeds(self):
        sub = {"bangumi_id": 21, "active": 1,
               "primary": {"rss_url": "https://primary", "offset": 0},
               "backup": {"rss_url": "https://backup", "offset": 0}}
        items = {"primary": [{"title": f"primary {i}"} for i in range(3)],
                 "backup": [{"title": f"backup {i}"} for i in range(2)]}

        async def select(*args, **kwargs):
            return items[kwargs["source"]]

        with patch.object(downloader, "list_subscriptions", return_value=[sub]), \
             patch.object(downloader.rss_service, "fetch_rss_snapshot", new_callable=AsyncMock,
                          return_value={"items": []}), \
             patch.object(downloader, "_fetch_passed_items", side_effect=select), \
             patch.object(downloader, "_download_item", new_callable=AsyncMock,
                          return_value=True) as download, \
             patch.object(downloader, "_refresh_sortrange", new_callable=AsyncMock) as refresh, \
             patch.object(downloader, "_check_completion", new_callable=AsyncMock) as completion:
            self.assertEqual(await downloader._process_subscription(sub), 5)

        self.assertEqual(download.await_count, 5)
        refresh.assert_awaited_once_with(21, sub)
        completion.assert_awaited_once_with(21, sub)

    async def test_subscription_skips_refresh_without_episode_change(self):
        sub = {"bangumi_id": 22, "active": 1,
               "primary": {"rss_url": "https://primary", "offset": 0},
               "backup": {"rss_url": "https://backup", "offset": 0}}
        with patch.object(downloader, "list_subscriptions", return_value=[sub]), \
             patch.object(downloader.rss_service, "fetch_rss_snapshot", new_callable=AsyncMock,
                          return_value={"items": []}), \
             patch.object(downloader, "_fetch_passed_items", new_callable=AsyncMock,
                          return_value=[{"title": "skipped"}]), \
             patch.object(downloader, "_download_item", new_callable=AsyncMock,
                          return_value=False) as download, \
             patch.object(downloader, "_refresh_sortrange", new_callable=AsyncMock) as refresh, \
             patch.object(downloader, "_check_completion", new_callable=AsyncMock) as completion:
            self.assertEqual(await downloader._process_subscription(sub), 0)

        self.assertEqual(download.await_count, 2)
        refresh.assert_not_awaited()
        completion.assert_not_awaited()

    async def test_single_poll_only_processes_requested_subscription(self):
        subs = [{"bangumi_id": 1, "active": 1}, {"bangumi_id": 2, "active": 1}]
        with patch.object(downloader, "list_subscriptions", return_value=subs), patch.object(
            downloader, "_process_subscription", new_callable=AsyncMock, return_value=3,
        ) as process:
            result = await downloader.poll_subscription(2)
        process.assert_awaited_once_with(subs[1], strict=True, primary_feed=None, backup_feed=None)
        self.assertEqual(result["downloaded"], 3)

    async def test_enrichment_is_saved_before_single_poll(self):
        sub = {"bangumi_id": 12, "primary": {"rss_url": "https://feed"}, "backup": {}}
        result = {"bgm": {"season": 1}, "tmdb": {"id": 4},
                  "primary_offset": 0, "backup_offset": None}
        events = []

        def save(*args):
            events.append("save")
            return True

        async def poll(*args, **kwargs):
            events.append("poll")
            return {"state": "completed", "message": "首次轮询完成", "downloaded": 2}

        with patch.object(initial_rss_poll.rss_service, "fetch_rss_snapshot", new_callable=AsyncMock, return_value={"items": []}), \
             patch.object(initial_rss_poll.data, "list_subscriptions", return_value=[sub]), \
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
        with patch.object(initial_rss_poll.rss_service, "fetch_rss_snapshot", new_callable=AsyncMock, return_value={"items": []}), \
             patch.object(initial_rss_poll.data, "list_subscriptions", return_value=[sub]), \
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
             patch.object(downloader.rss_service, "fetch_rss_snapshot", new_callable=AsyncMock, return_value={"items": []}), \
             patch.object(downloader, "_fetch_passed_items", new_callable=AsyncMock,
                          return_value=[]) as fetch, \
             patch.object(downloader, "_compute_rss_offset") as compute:
            self.assertEqual(await downloader._process_subscription(stale), 0)
        self.assertEqual(fetch.await_args.kwargs["rss_offset"], 0)
        compute.assert_not_called()

    async def test_missing_zero_offset_is_recomputed_and_saved(self):
        sub = {"bangumi_id": 12, "active": 1,
               "bgm": {"sortrange": [2, 12], "air_date": "2026-09-25"},
               "primary": {"rss_url": "https://feed"}, "backup": {}}
        with patch.object(downloader, "list_subscriptions", return_value=[sub]), \
             patch.object(downloader, "_compute_rss_offset",
                          return_value=2), \
             patch("backend.data.set_subscription_rss_offset", return_value=True) as save, \
             patch.object(downloader.rss_service, "fetch_rss_snapshot", new_callable=AsyncMock, return_value={"items": []}), \
             patch.object(downloader, "_fetch_passed_items", new_callable=AsyncMock,
                          return_value=[]) as fetch:
            self.assertEqual(await downloader._process_subscription(sub), 0)
        save.assert_called_once_with(12, "primary", 0)
        self.assertEqual(fetch.await_args.kwargs["rss_offset"], 0)

    async def test_initial_poll_fetches_each_distinct_url_once(self):
        for backup_url, expected in (("", 1), ("https://backup", 2),
                                     ("https://primary", 1)):
            with self.subTest(backup_url=backup_url):
                sub = {"bangumi_id": 19, "primary": {"rss_url": "https://primary"},
                       "backup": {"rss_url": backup_url}}
                result = {"bgm": {"season": 1}, "tmdb": {"id": 4},
                          "primary_offset": 0,
                          "backup_offset": 0 if backup_url else None}
                snapshot = {"items": [{"episode_number": 2}]}
                with patch.object(initial_rss_poll.data, "list_subscriptions", return_value=[sub]), \
                     patch.object(initial_rss_poll.data, "update_subscription", return_value=True), \
                     patch.object(initial_rss_poll.data, "set_subscription_rss_offset", return_value=True), \
                     patch.object(initial_rss_poll.rss_service, "fetch_rss_snapshot",
                                  new_callable=AsyncMock, return_value=snapshot) as fetch, \
                     patch.object(downloader, "enrich_subscription", new_callable=AsyncMock,
                                  return_value=result) as enrich, \
                     patch.object(downloader, "poll_subscription", new_callable=AsyncMock,
                                  return_value={"state": "completed", "downloaded": 0}) as poll:
                    initial_rss_poll._jobs[19] = {}
                    await initial_rss_poll._run(19)
                self.assertEqual(fetch.await_count, expected)
                self.assertIs(enrich.await_args.kwargs["primary_feed"], snapshot)
                self.assertIs(poll.await_args.kwargs["primary_feed"], snapshot)
                if backup_url == "https://primary":
                    self.assertIs(poll.await_args.kwargs["backup_feed"], snapshot)

    async def test_scheduler_reuses_snapshot_for_missing_offset(self):
        sub = {"bangumi_id": 20, "active": 1,
               "bgm": {"sortrange": [2, 12], "air_date": "2026-09-25"},
               "primary": {"rss_url": "https://feed"}, "backup": {}}
        snapshot = {"items": [{"episode_number": 2, "pub_date": "2026-09-25"}]}
        with patch.object(downloader, "list_subscriptions", return_value=[sub]), \
             patch.object(downloader.rss_service, "fetch_rss_snapshot", new_callable=AsyncMock,
                          return_value=snapshot) as fetch, \
             patch("backend.data.set_subscription_rss_offset", return_value=True), \
             patch.object(downloader, "_fetch_passed_items", new_callable=AsyncMock,
                          return_value=[]) as select:
            await downloader._process_subscription(sub)
        fetch.assert_awaited_once_with("https://feed")
        self.assertIs(select.await_args.kwargs["feed"], snapshot)
        self.assertEqual(select.await_args.kwargs["rss_offset"], 0)


if __name__ == "__main__":
    unittest.main()
