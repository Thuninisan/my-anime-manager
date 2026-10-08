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


    async def test_preview_japanese_plot_does_not_skip_chinese_lookup(self):
        from tests.test_episode_metadata import mapping, candidates
        ref = mapping()
        available = candidates()
        available['tmdb']['plot'] = 'これは日本語のあらすじです。'
        ctx = MetadataContext()
        ctx.preview_snapshot = {'episode_metadata': {
            f"{provider}:{ref[provider]['episode_id']}": candidate
            for provider, candidate in available.items() if candidate is not None
        }}
        ctx.tmdb_selected_seasons[100] = {1}
        chinese = {1: {'episodes': [{'tmdbId': ref['tmdb']['episode_id'],
                                    'epNum': 3, 'overview': '这是正确的中文简介。'}]}}
        with patch.object(ctx, 'get_tmdb_detail', AsyncMock(return_value={})), \
             patch.object(tmdb, 'build_season_episode_map', AsyncMock(return_value=chinese)) as build, \
             patch.object(ctx, 'get_tvdb_episode_translation', AsyncMock()) as tvdb:
            for _ in range(2):
                result = await resolve_episode_plot(tmdb_id=100, tmdb_season=1, tmdb_ep_num=3,
                    tvdb_id=300, tvdb_season=1, tvdb_episode_number=1,
                    metadata_ctx=ctx, episode_mapping=ref)
                self.assertEqual(result, '这是正确的中文简介。')
            build.assert_awaited_once()
            self.assertEqual(build.await_args.kwargs['season_numbers'], {1})
            tvdb.assert_not_awaited()

    async def test_selected_special_season_is_fetched_once(self):
        from unittest.mock import Mock
        response = Mock()
        response.json.return_value = {'episodes': [{'id': 1, 'episode_number': 1,
            'season_number': 0, 'name': '特别篇', 'overview': '特别篇中文简介。'}]}
        with patch('backend.clients.tmdb.get_season_detail', AsyncMock(return_value=response)) as fetch:
            result = await tmdb.build_season_episode_map(123, language='zh-CN',
                tv_detail={'number_of_seasons': 3}, season_numbers={0}, strict=True)
            self.assertEqual(set(result), {0})
            fetch.assert_awaited_once_with(123, 0, language='zh-CN')

    async def test_tvdb_missing_translation_is_cached_but_server_error_retries(self):
        import httpx
        request = httpx.Request('GET', 'https://example.test/translation')
        missing = httpx.HTTPStatusError('missing', request=request,
            response=httpx.Response(404, request=request))
        ctx = MetadataContext()
        with patch('backend.clients.tvdb.get_episode_translations', AsyncMock(side_effect=missing)) as fetch:
            self.assertEqual(await ctx.get_tvdb_episode_translation(1), {})
            self.assertEqual(await ctx.get_tvdb_episode_translation(1), {})
            fetch.assert_awaited_once()
        failure = httpx.HTTPStatusError('failed', request=request,
            response=httpx.Response(500, request=request))
        with patch('backend.clients.tvdb.get_episode_translations', AsyncMock(side_effect=failure)) as fetch:
            for _ in range(2):
                with self.assertRaises(httpx.HTTPStatusError):
                    await ctx.get_tvdb_episode_translation(2)
            self.assertEqual(fetch.await_count, 2)


    async def test_preview_catalog_is_seeded_as_japanese(self):
        from backend.domain.episode_metadata_adapters import seed_provider_catalogs
        ctx = MetadataContext()
        catalog = {1: {'episodes': [{'overview': '日本語のあらすじ'}]}}
        seed_provider_catalogs(ctx, {'tmdb': {'123': catalog}})
        self.assertIs(ctx.tmdb_season_maps[(123, 'ja')], catalog)
        self.assertNotIn((123, 'zh-CN'), ctx.tmdb_season_maps)
