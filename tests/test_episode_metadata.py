"""Canonical metadata policy, provenance and old writer XML compatibility."""
import copy
import itertools
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.domain.episode import create_episode_mapping
from backend.domain.episode_metadata_adapters import provider_metadata_candidates
from tests.legacy_helpers import legacy_batch_episode_mapping, legacy_download_episode_mapping, mapping_to_legacy_batch_episode, seed_preview_metadata
from backend.services.episode_metadata_resolver import resolve_episode, resolve_nfo_episode
from backend.services.nfo.nfo_xml import generate_episode_nfo
from backend.services.nfo.metadata_context import MetadataContext
from backend.services.nfo.generator import batch_nfo_generator
from backend.services.torrent.metadata import pre_generate_nfo
from backend import config


def mapping(season_number=1, episode_number=3, *, tmdb_id=100, tvdb_id=300, subject_id=200):
    return create_episode_mapping(
        {"season_number": 9, "episode_number": 99},
        {"subject_id": subject_id, "episode_id": 201, "episode_number": 2.5, "episode_absolute": 0},
        {"series_id": tmdb_id, "episode_id": 101, "season_number": 1, "episode_number": 3},
        {"series_id": tvdb_id, "episode_id": 301, "season_number": season_number, "episode_number": episode_number},
        "tmdb",
    )


def candidates():
    return provider_metadata_candidates(
        {"tmdbId": 101, "name": "TMDB title", "overview": "这是本集简介。", "airDate": "2026-01-02",
         "runtime": 24, "voteAverage": 8.2, "voteCount": 0, "stillPath": "/tmdb.jpg",
         "guestStars": [{"name": "Actor", "character": "Hero"}], "directors": ["Director"], "writers": ["Writer"]},
        {"tvdbId": 301, "name": "TVDB title", "overview": "来自 TVDB 的中文简介。", "siteRating": 7.5,
         "airDate": "1999-01-01", "runtime": 90, "stillPath": "/tvdb.jpg"},
        {"id": 201, "name": "原名", "name_cn": "中文标题", "desc": "翻译后的简介。"},
    )


class MetadataResolverTests(unittest.TestCase):
    def test_all_provider_subsets_and_partial_metadata(self):
        all_candidates = candidates()
        for count in range(1, 4):
            for providers in itertools.combinations(all_candidates, count):
                with self.subTest(providers=providers):
                    available = {provider: value if provider in providers else None for provider, value in all_candidates.items()}
                    resolved = resolve_episode(mapping(), available)
                    metadata = resolved['metadata']
                    self.assertEqual(metadata['title'], '中文标题' if 'bangumi' in providers else 'TMDB title' if 'tmdb' in providers else '')
                    self.assertEqual(metadata['rating'], 7.5 if 'tvdb' in providers else 8.2 if 'tmdb' in providers else None)
                    self.assertEqual(metadata['air_date'], '2026-01-02' if 'tmdb' in providers else None)
                    self.assertEqual(metadata['runtime_minutes'], 24 if 'tmdb' in providers else None)
        partial = candidates()
        partial['tmdb']['plot'] = None
        partial['tmdb']['still_path'] = ''
        resolved = resolve_episode(mapping(), partial)
        self.assertEqual(resolved['metadata']['plot'], '来自 TVDB 的中文简介。')
        self.assertEqual(resolved['metadata']['still_path'], '/tvdb.jpg')
        self.assertEqual(resolved['provenance']['plot'], 'tvdb')
        self.assertEqual(resolved['provenance']['still_path'], 'tvdb')
        self.assertEqual(resolved['provenance']['title'], 'bangumi')

    def test_coordinates_manual_override_zero_and_match_source_are_independent(self):
        reference = mapping(0, 0)
        before = copy.deepcopy(reference)
        resolved = resolve_episode(reference, candidates())
        self.assertEqual((resolved['season_number'], resolved['episode_number']), (0, 0))
        self.assertEqual(resolved['mapping'], before)
        self.assertEqual(resolved['mapping']['bangumi']['episode_number'], 2.5)
        self.assertEqual(resolved['mapping']['bangumi']['episode_absolute'], 0)
        self.assertEqual(resolved['provenance']['season_number'], 'tvdb')
        reference['tvdb']['season_number'] = None
        resolved = resolve_episode(reference, candidates())
        self.assertEqual((resolved['season_number'], resolved['episode_number']), (1, 3))
        reference['tmdb']['season_number'] = 2
        reference['tmdb']['episode_number'] = 15
        resolved = resolve_episode(reference, candidates())
        self.assertEqual((resolved['season_number'], resolved['episode_number']), (2, 15))

    def test_metadata_is_lossless_and_numeric_zero_is_present(self):
        available = candidates()
        available['tvdb']['rating'] = 0.0
        available['tmdb']['runtime_minutes'] = 0
        resolved = resolve_episode(mapping(), available)
        self.assertEqual(resolved['metadata']['rating'], 0.0)
        self.assertEqual(resolved['provenance']['rating'], 'tvdb')
        self.assertEqual(resolved['metadata']['runtime_minutes'], 0)
        self.assertEqual(resolved['metadata']['vote_count'], 0)
        self.assertEqual(resolved['metadata']['guest_stars'], [{'name': 'Actor', 'character': 'Hero'}])
        self.assertEqual(available['tmdb']['provider_episode_id'], 101)
        self.assertEqual(available['tvdb']['provider_episode_id'], 301)
        self.assertEqual(available['bangumi']['provider_episode_id'], 201)
        self.assertEqual(resolved['metadata']['directors'], ['Director'])
        self.assertEqual(resolved['metadata']['writers'], ['Writer'])
        self.assertEqual(resolved['metadata']['original_title'], '原名')
        roles = provider_metadata_candidates(tmdb={'guest_stars': [{'name': 'Name', 'role': 'Role'}]})
        self.assertEqual(roles['tmdb']['guest_stars'][0]['character'], 'Role')

    def test_missing_coordinates_fail_explicitly_without_synthesizing_them(self):
        absent = create_episode_mapping({'season_number': 1, 'episode_number': 3})
        with self.assertRaisesRegex(ValueError, 'mapping required field'):
            resolve_episode(absent, candidates())
        with self.assertRaisesRegex(ValueError, 'missing mapping'):
            resolve_episode(None, candidates())
        with self.assertRaisesRegex(ValueError, 'missing metadata'):
            resolve_episode(mapping(), None)
        available = {'bangumi': None, 'tmdb': None, 'tvdb': None}
        self.assertEqual(resolve_episode(mapping(), available)['metadata']['title'], '')

    def test_legacy_boundary_zero_and_identity_lookup(self):
        file = {'bangumi_id': 200, 'bangumi_ep_id': 201, 'bangumi_sort': 0,
                'tmdb_show_name': 'B', 'tmdb_season': 0, 'tmdb_episode': 0,
                'tvdb_season': 0, 'tvdb_episode': 0}
        preview = {'search_results': {'A': {'tmdb': {'id': 10, 'name': 'A'}},
                   'B': {'tmdb': {'id': 20, 'name': 'B'}, 'map_entries': [{'bangumi_id': 200, 'tvdb_id': 30}]}}}
        result = legacy_download_episode_mapping(file, preview)
        self.assertEqual(result['tmdb']['series_id'], 20)
        self.assertEqual(result['tvdb']['series_id'], 30)
        self.assertEqual(result['tmdb']['season_number'], 0)
        self.assertEqual(result['bangumi']['episode_absolute'], 0)
        canonical = mapping()
        self.assertIs(legacy_download_episode_mapping({'episode_mapping': canonical}, preview), canonical)
        legacy = mapping_to_legacy_batch_episode(canonical)
        self.assertIs(legacy_batch_episode_mapping(legacy), canonical)

    def test_seed_preview_does_not_mutate_or_discard_metadata(self):
        ctx = MetadataContext()
        raw = {'episode_data': {'tmdb': {'100': {'0': {'episodes': [{'guestStars': [{'name': 'A', 'character': 'R'}]}]}}},
                               'tvdb': {'300': {'seasons': {}}}, 'bangumi': {'200': {'episodes': [{'sort': 0}]}}}}
        seed_preview_metadata(ctx, raw)
        self.assertEqual(ctx.tmdb_season_maps[(100, 'zh-CN')]['0']['episodes'][0]['guestStars'][0]['character'], 'R')
        self.assertEqual(ctx.bgm_episodes[200][0]['sort'], 0)


class NfoGoldenTests(unittest.TestCase):
    def test_xml_matches_pre_refactor_writer_fixtures(self):
        # Fixtures were captured from the old XML writer before this refactor.
        cases = {'regular': {}, 'special': {'season_number': 0},
                 'missing_tmdb': {'no_tmdb': True}, 'missing_tvdb': {'no_tvdb': True},
                 'bangumi_only': {'no_tmdb': True, 'no_tvdb': True},
                 'manual_override': {'season_number': 2, 'episode_number': 15}, 'guests': {}}
        for case, options in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                available = candidates()
                available['tmdb']['still_path'] = 'episode.jpg'
                if options.get('no_tmdb'):
                    available['tmdb'] = None
                if options.get('no_tvdb'):
                    available['tvdb'] = None
                # Golden regular rating is TVDB's 8.2, missing TMDB is 7.5.
                if available['tvdb'] and not options.get('no_tmdb'):
                    available['tvdb']['rating'] = 8.2
                ref = mapping(options.get('season_number', 1), options.get('episode_number', 3))
                if options.get('no_tvdb'):
                    ref['tvdb']['episode_id'] = 0
                resolved = resolve_episode(ref, available)
                if case == 'bangumi_only':
                    resolved['metadata']['plot'] = '翻译后的简介。'
                written = generate_episode_nfo(resolved, show_name='测试番剧 & Show', bangumi_subject_name='作品原名',
                    thumb_path='' if case == 'bangumi_only' else 'episode.jpg', output_dir=tmp, file_stem='episode')
                actual = ET.canonicalize(Path(written).read_text(), strip_text=True)
                expected = ET.canonicalize((Path(__file__).parent / 'fixtures' / 'episode_nfo' / f'{case}.xml').read_text(), strip_text=True)
                self.assertEqual(actual, expected)

    def test_writer_escapes_values_preserves_zero_and_never_fetches(self):
        resolved = resolve_episode(mapping(0, 0), candidates())
        resolved['metadata']['title'] = '<Title & Name>'
        resolved['metadata']['runtime_minutes'] = 0
        resolved['mapping']['bangumi']['episode_id'] = 0
        with tempfile.TemporaryDirectory() as tmp:
            path = generate_episode_nfo(resolved, show_name='Show', bangumi_subject_name='Subject', output_dir=tmp, file_stem='episode')
            xml = ET.parse(path).getroot()
            self.assertEqual(xml.findtext('title'), '<Title & Name>')
            self.assertEqual(resolved['metadata']['runtime_minutes'], 0)
            self.assertEqual(xml.findtext('runtime'), '')  # legacy XML representation
            for tag in ('season', 'episode', 'bangumiid'):
                self.assertEqual(xml.findtext(tag), '0')


class AsyncResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_chinese_metadata_requires_no_fallback_requests(self):
        with patch('backend.services.nfo.plot_fallback.resolve_episode_plot', AsyncMock()) as fallback:
            result = await resolve_nfo_episode(mapping(), candidates())
        fallback.assert_not_awaited()
        self.assertEqual(result['provenance']['title'], 'bangumi')
        self.assertEqual(result['provenance']['plot'], 'tmdb')

    async def test_title_translation_failure_preserves_legacy_tmdb_fallback(self):
        available = candidates()
        available['bangumi']['title'] = ''
        available['bangumi']['original_title'] = 'かなタイトル'
        with patch('backend.services.nfo.translate.chat', AsyncMock(side_effect=RuntimeError('offline'))):
            result = await resolve_nfo_episode(mapping(), available)
        self.assertEqual(result['metadata']['title'], 'TMDB title')
        self.assertEqual(result['metadata']['original_title'], 'かなタイトル')
        self.assertEqual(result['provenance']['title'], 'tmdb')

    async def test_bangumi_zero_sort_translation_uses_mapping_episode_id(self):
        available = candidates()
        available['tmdb'] = None
        available['tvdb'] = None
        ctx = MetadataContext()
        ctx.tmdb_season_maps[(100, 'zh-CN')] = {}
        ctx.tvdb_series[(300, 'zho')] = {}
        ctx.bgm_episodes[200] = [{'id': 999, 'sort': 0, 'ep': 9, 'desc': 'wrong'},
                                {'id': 201, 'sort': 0, 'ep': 2.5, 'desc': '正しいあらすじ'}]
        with patch('backend.services.nfo.plot_fallback.translate_ja_to_zh', AsyncMock(return_value='翻译后的简介。')) as translate:
            result = await resolve_nfo_episode(mapping(), available, metadata_ctx=ctx)
        translate.assert_awaited_once_with('正しいあらすじ')
        self.assertEqual(result['provenance']['plot'], 'bangumi:translated')

    async def test_multiseries_preview_uses_mapping_and_reuses_catalogs(self):
        preview = {'search_results': {'first': {'tmdb': {'id': 999, 'name': 'Wrong'}}},
                   'episode_data': {'tmdb': {'100': {'1': {'episodes': []}}, '110': {'1': {'episodes': []}}}}}
        preview = {'series_contexts': {}, 'episode_metadata': {}}
        first, second = mapping(), mapping(tmdb_id=110, tvdb_id=310, subject_id=210)
        with patch('backend.services.nfo.generator.batch_nfo_generator', AsyncMock(return_value={'nfoGenerated': 2})) as generate:
            result = await pre_generate_nfo(preview, [{'episode_mapping': first}, {'episode_mapping': second}], 'Torrent', '/unused', '')
        self.assertTrue(result[1])
        episodes = generate.await_args.args[1]
        self.assertEqual([ep['episode_mapping']['tmdb']['series_id'] for ep in episodes], [100, 110])
        self.assertEqual([ep['episode_mapping']['tvdb']['series_id'] for ep in episodes], [300, 310])
        context = generate.await_args.kwargs['metadata_ctx']
        with patch('backend.services.tmdb.build_season_episode_map', AsyncMock()) as fetch:
            await context.get_tmdb_season_map(100, 'zh-CN')
            await context.get_tmdb_season_map(110, 'zh-CN')
        fetch.assert_not_awaited()

    async def test_batch_manual_mapping_specials_and_metadata(self):
        ctx = MetadataContext()
        ctx.bgm_episodes[200] = [{'id': 201, 'ep': 2.5, 'raw_sort': 0, 'sort': 9, 'name_cn': '中文标题', 'name': '原名'}]
        ctx.bgm_subjects[200] = {'name_cn': '测试番剧'}
        ctx.tmdb_details[(100, 'zh-CN')] = {'name': '测试番剧', 'overview': '作品简介。'}
        ctx.tmdb_season_maps[(100, 'zh-CN')] = {'1': {'episodes': [{'tmdbId': 101, 'epNum': 3, 'name': 'TMDB', 'overview': '本集中文简介。', 'airDate': '2026-01-02', 'runtime': 24}]}}
        ctx.tvdb_series[(300, 'jpn')] = {'seasons': {'0': {'episodes': [{'tvdbId': 301, 'epNum': 0, 'siteRating': 7.5, 'stillPath': 'tv.jpg'}]}}}
        ref = mapping(0, 0)
        before = copy.deepcopy(ref)
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, 'RSS_PATH_TEMPLATE', '{series_name}/Season {tvdb_season:02d}/{tmdb_title} {tvdb_episode:02d}'), \
             patch('backend.services.nfo.images.download_show_images', AsyncMock(return_value={})), \
             patch('backend.services.nfo.images.download_tvdb_episode_thumb', AsyncMock(return_value='episode.jpg')) as thumb:
            summary = await batch_nfo_generator(tmp, [{'episode_mapping': ref}], series_name='测试番剧', metadata_ctx=ctx)
            self.assertEqual(summary['episodesProcessed'], 1)
            episode = next(Path(tmp).rglob('测试番剧 00.nfo'))
            xml = ET.parse(episode).getroot()
            self.assertEqual(xml.findtext('title'), '中文标题')
            self.assertEqual(xml.findtext('season'), '0')
            self.assertEqual(xml.findtext('episode'), '0')
            self.assertEqual(xml.findtext('bangumiid'), '201')
            self.assertEqual(xml.findtext('tvdbid'), '301')
            thumb.assert_awaited_once()
        self.assertEqual(ref, before)


class CatalogBoundaryTests(unittest.TestCase):
    def test_exact_ids_do_not_rematch_by_number_or_mix_provider_seasons(self):
        from backend.domain.episode_metadata_adapters import metadata_candidates_from_catalogs
        ref = mapping(0, 3)
        ref['tmdb']['season_number'] = 7  # manual numbering differs from provider catalog
        raw_tmdb = {'1': {'episodes': [{'tmdbId': 999, 'epNum': 3, 'name': 'Wrong'},
                                      {'tmdbId': 101, 'epNum': 4, 'name': 'Selected'}]}}
        available = metadata_candidates_from_catalogs(ref, raw_tmdb, {}, [])
        self.assertEqual(available['tmdb']['title'], 'Selected')
        self.assertEqual(ref['tmdb']['season_number'], 7)
        ref['tvdb']['episode_id'] = None
        ref['tvdb']['season_number'] = None
        available = metadata_candidates_from_catalogs(ref, {}, {'seasons': {'7': {'episodes': [{'epNum': 3, 'tvdbId': 301}]}}}, [])
        self.assertIsNone(available['tvdb'])

    def test_ambiguous_legacy_provider_identity_is_an_explicit_error(self):
        file = {'bangumi_id': 200, 'tmdb_show_name': 'Common'}
        preview = {'search_results': {'a': {'tmdb': {'id': 10, 'name': 'Common'}},
                                      'b': {'tmdb': {'id': 20, 'name': 'Common'}}}}
        with self.assertRaisesRegex(ValueError, 'ambiguous provider identity'):
            legacy_download_episode_mapping(file, preview)

    def test_path_template_preserves_explicit_zero_but_keeps_omitted_legacy_fallback(self):
        from backend.services.nfo.generator import format_download_path
        template = '{bangumi_sort}/{bangumi_ep}/{tvdb_episode}/{tmdb_episode}'
        self.assertEqual(format_download_path(template, {}, sort=9, bangumi_sort=0, bangumi_ep=0, tvdb_episode=0, tmdb_episode=0), '0/0/0/0')
        self.assertEqual(format_download_path(template, {}, sort=9), '9/9/9/9')


class PlotPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_tvdb_chinese_is_chosen_before_bangumi_translation(self):
        available = candidates()
        available['tmdb']['plot'] = 'Japanese あらすじ'
        available['tvdb']['plot'] = ''
        ctx = MetadataContext()
        ctx.tmdb_season_maps[(100, 'zh-CN')] = {'1': {'episodes': [{'epNum': 3, 'overview': 'Japanese あらすじ'}]}}
        ctx.tvdb_series[(300, 'zho')] = {'episodes': [{'seasonNumber': 1, 'number': 3, 'overview': 'TVDB 的中文简介。'}]}
        with patch.object(ctx, 'get_bgm_episodes', AsyncMock()) as bangumi_fetch:
            result = await resolve_nfo_episode(mapping(), available, metadata_ctx=ctx)
        bangumi_fetch.assert_not_awaited()
        self.assertEqual(result['metadata']['plot'], 'TVDB 的中文简介。')
        self.assertEqual(result['provenance']['plot'], 'tvdb')

    async def test_title_translation_and_foreign_plot_provenance(self):
        available = candidates()
        available['bangumi']['title'] = ''
        available['bangumi']['original_title'] = '新しいタイトル'
        available['tmdb']['plot'] = 'Foreign synopsis'
        with patch('backend.services.nfo.translate.chat', AsyncMock(return_value='新的标题')), \
             patch('backend.services.nfo.plot_fallback.resolve_episode_plot', AsyncMock(return_value='翻译后的中文简介。')) as plot:
            result = await resolve_nfo_episode(mapping(0, 0), available)
        self.assertEqual(result['metadata']['title'], '新的标题')
        self.assertEqual(result['provenance']['title'], 'bangumi:translated')
        self.assertEqual(result['metadata']['sources']['title'], 'translated')
        self.assertEqual(plot.await_args.kwargs['tvdb_season'], 0)
        self.assertEqual(plot.await_args.kwargs['tvdb_episode_number'], 0)
        self.assertEqual(plot.await_args.kwargs['bangumi_sort'], 0)
        self.assertEqual(plot.await_args.kwargs['bangumi_episode_id'], 201)

    async def test_canonical_file_writer_uses_supplied_mapping(self):
        ref = mapping(0, 15)
        with tempfile.TemporaryDirectory() as tmp:
            resolved = resolve_episode(ref, provider_metadata_candidates(tmdb={}))
            written = generate_episode_nfo(resolved, show_name='Show',
                bangumi_subject_name='Subject', output_dir=tmp, file_stem='episode')
            xml = ET.parse(written).getroot()
            self.assertEqual(xml.findtext('season'), '0')
            self.assertEqual(xml.findtext('episode'), '15')
            self.assertEqual(xml.findtext('bangumiid'), '201')
            self.assertEqual(xml.findtext('tvdbid'), '301')


class DownloadMappingTests(unittest.TestCase):
    def test_processing_and_nfo_share_specials_and_bangumi_path_coordinates(self):
        from tests.legacy_helpers import download_entry_with_mapping
        from backend.services.torrent.monitor import build_processing
        ref = mapping(0, 0)
        file = {'torrent_path': 'source.mkv', 'tmdb_show_name': 'Show', 'bangumi_show_name': 'Subject',
                'episode_mapping': ref, 'tmdb_season': 99, 'tmdb_episode': 99, 'tvdb_season': 99,
                'tvdb_episode': 99, 'bangumi_sort': 99, 'bangumi_id': 999}
        projected = download_entry_with_mapping(file)
        self.assertEqual(projected['bangumi_id'], 200)
        self.assertEqual(projected['bangumi_sort'], 0)
        self.assertEqual(projected['tvdb_season'], 0)
        with patch.object(config, 'RSS_PATH_TEMPLATE', '{series_name}/Season {tvdb_season:02d}/{bangumi_sort}-{bangumi_ep}-{tvdb_episode}-{tmdb_episode}'):
            processing = build_processing({'hardlink_root': '/downloads', 'torrent_name': 'Torrent',
                                           'series_name': 'Show', 'files': [file]})
        self.assertEqual(processing['files'][0]['target_path'], '/downloads/Show/Season 00/0-2-0-3.mkv')
        self.assertEqual(file['tvdb_season'], 99)  # boundary does not mutate caller input
