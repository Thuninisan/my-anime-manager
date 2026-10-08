"""Regression checks for poll-scoped metadata reuse and cross-season matching."""
import unittest
from unittest.mock import AsyncMock, patch

from backend.services.nfo.metadata_context import MetadataContext
from backend.services.nfo.plot_fallback import resolve_episode_plot
from backend.services import enrich, tmdb


class MetadataContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_language_reuses_full_map_and_other_language_is_independent(self):
        ctx = MetadataContext()
        with patch.object(ctx, 'get_tmdb_detail', AsyncMock(return_value={'number_of_seasons': 3})), \
             patch.object(tmdb, 'build_season_episode_map', AsyncMock(return_value={1: {}})) as build:
            self.assertIs(await ctx.get_tmdb_season_map(123, 'ja'),
                          await ctx.get_tmdb_season_map(123, 'ja'))
            self.assertEqual(build.await_count, 1)
            await ctx.get_tmdb_season_map(123, 'zh-CN')
            self.assertEqual(build.await_count, 2)
            self.assertEqual([call.kwargs['language'] for call in build.await_args_list],
                             ['ja', 'zh-CN'])

    async def test_twelve_episodes_reuse_one_chinese_map(self):
        ctx = MetadataContext()
        with patch.object(ctx, 'get_tmdb_detail', AsyncMock(return_value={'number_of_seasons': 3})), \
             patch.object(tmdb, 'build_season_episode_map', AsyncMock(return_value={1: {}})) as build:
            for _ in range(12):
                await ctx.get_tmdb_season_map(123, 'zh-CN')
            build.assert_awaited_once()

    async def test_cross_season_inference_and_offset_share_japanese_map(self):
        ctx = MetadataContext()
        episodes = [{'name': '最終決戦', 'sort': 1}]
        season_map = {
            1: {'episodes': [{'epNum': 1, 'name': '別の話'}]},
            2: {'episodes': [{'epNum': 1, 'name': '異なる話'}]},
            3: {'episodes': [{'epNum': 2, 'name': '最終決戦'}]},
        }
        with patch.object(enrich, 'get_tmdb_id', return_value=123), \
             patch.object(ctx, 'get_bgm_episodes', AsyncMock(return_value=episodes)), \
             patch.object(ctx, 'get_tmdb_detail', AsyncMock(return_value={'original_language': 'ja'})), \
             patch.object(tmdb, 'build_season_episode_map', AsyncMock(return_value=season_map)) as build:
            result = await enrich._auto_infer_tmdb(456, [111, 456], {}, lambda _: None, ctx)
            self.assertEqual(result['tmdb_season'], 3)
            offset = await enrich._compute_tmdb_ep_offset(456, 123, 3, lambda _: None, ctx)
            self.assertEqual(offset, 1)
            build.assert_awaited_once()

    async def test_plot_uses_cached_sources_without_client_fetch(self):
        ctx = MetadataContext()
        ctx.tmdb_season_maps[(123, 'zh-CN')] = {1: {'episodes': [{'epNum': 1, 'overview': '这是中文简介。'}]}}
        ctx.tvdb_series[(456, 'zho')] = {'episodes': []}
        ctx.bgm_episodes[789] = []
        with patch.object(ctx, 'get_tmdb_detail', AsyncMock()) as detail, \
             patch('backend.services.nfo.plot_fallback._try_tmdb_zh', AsyncMock()) as tmdb_fetch, \
             patch('backend.services.nfo.plot_fallback._try_tvdb_zh', AsyncMock()) as tvdb_fetch:
            result = await resolve_episode_plot(tmdb_id=123, tmdb_season=1, tmdb_ep_num=1,
                                                tvdb_id=456, tvdb_season=1, tvdb_episode_number=1,
                                                bangumi_id=789, bangumi_sort=1,
                                                metadata_ctx=ctx)
            self.assertEqual(result, '这是中文简介。')
            detail.assert_not_awaited()
            tmdb_fetch.assert_not_awaited()
            tvdb_fetch.assert_not_awaited()
