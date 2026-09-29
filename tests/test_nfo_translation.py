"""Offline regression tests; isolate external clients and package side effects."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative, dependencies):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, dependencies):
        spec.loader.exec_module(module)
    return module


client = types.ModuleType('backend.clients.deepseek')
client.chat = AsyncMock()
translate = load_module(
    'backend.services.nfo.translate',
    'backend/services/nfo/translate.py',
    {client.__name__: client},
)
clients = types.ModuleType('backend.clients')
clients.tmdb = types.SimpleNamespace()
clients.tvdb = types.SimpleNamespace()
enrich = types.ModuleType('backend.services.enrich')
enrich._get_bangumi_episodes = AsyncMock()
fallback = load_module(
    'backend.services.nfo.plot_fallback',
    'backend/services/nfo/plot_fallback.py',
    {clients.__name__: clients, enrich.__name__: enrich,
     translate.__name__: translate},
)


class TranslationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        translate._translation_cache.clear()
        translate._title_translation_cache.clear()

    async def test_title_prefers_bangumi_chinese_without_translation(self):
        with patch.object(translate, 'chat', AsyncMock()) as chat:
            self.assertEqual(await translate.resolve_episode_title(
                ' 中文集名 ', '日本語', 'TMDB集名'), '中文集名')
            chat.assert_not_awaited()

    async def test_title_translation_precedes_tmdb_and_has_separate_cache(self):
        translate._translation_cache['約束'] = '这是剧情简介。'
        with patch.object(translate, 'chat', AsyncMock(return_value='约定')) as chat:
            for _ in range(2):
                self.assertEqual(await translate.resolve_episode_title(
                    ' ', '約束', 'TMDB集名', show_name='番剧'), '约定')
            chat.assert_awaited_once()
            self.assertIn('episode title', chat.call_args.kwargs['system'])

    async def test_title_failure_falls_back_to_tmdb_without_caching(self):
        for value in [RuntimeError('offline'), TimeoutError(), '', '日本語の集名',
                      'English title', '标题\n解释']:
            mock = (AsyncMock(side_effect=value) if isinstance(value, Exception)
                    else AsyncMock(return_value=value))
            with patch.object(translate, 'chat', mock):
                self.assertEqual(await translate.resolve_episode_title(
                    '', '日本語の集名', '第 1 集'), '第 1 集')
                mock.assert_awaited_once()
                self.assertFalse(translate._title_translation_cache)

    async def test_title_without_bangumi_original_uses_tmdb(self):
        with patch.object(translate, 'chat', AsyncMock()) as chat:
            self.assertEqual(await translate.resolve_episode_title(
                '', ' ', 'TMDB集名'), 'TMDB集名')
            chat.assert_not_awaited()

    async def test_title_all_sources_missing_is_empty(self):
        self.assertEqual(await translate.resolve_episode_title('', '', ''), '')

    async def test_title_allows_unchanged_han_and_mixed_latin_names(self):
        for title in ['再会', 'AI的世界']:
            with patch.object(translate, 'chat', AsyncMock(return_value=title)):
                self.assertEqual(await translate.resolve_episode_title(
                    '', title, '第1集'), title)

    async def test_no_kana_is_not_a_translation_bypass(self):
        with patch.object(translate, 'chat', AsyncMock(return_value='最终决战')) as chat:
            self.assertEqual(await translate.translate_ja_to_zh('最終決戦'), '最终决战')
            chat.assert_awaited_once()

    async def test_mixed_japanese_and_english_are_retried(self):
        with patch.object(translate, 'chat', AsyncMock(side_effect=[
            '大家终于到达目的地，新的旅途即将开始。彼は出発。',
            'The heroes begin a new journey.', '英雄们开始了新的旅程。',
        ])) as chat:
            self.assertEqual(await translate.translate_ja_to_zh('彼らの旅が始まる。'),
                             '英雄们开始了新的旅程。')
            self.assertEqual(chat.await_count, 3)

    async def test_failure_never_returns_or_caches_source(self):
        for value in ['', '彼らの旅が始まる。', RuntimeError('offline')]:
            mock = AsyncMock(side_effect=value) if isinstance(value, Exception) else AsyncMock(return_value=value)
            with patch.object(translate, 'chat', mock):
                self.assertEqual(await translate.translate_ja_to_zh('彼らの旅が始まる。'), '')
                self.assertEqual(mock.await_count, translate.MAX_ATTEMPTS)
                self.assertFalse(translate._translation_cache)

    async def test_chinese_echo_and_cache(self):
        with patch.object(translate, 'chat', AsyncMock(return_value='新的旅程开始了。')) as chat:
            for _ in range(2):
                self.assertEqual(await translate.translate_ja_to_zh('新的旅程开始了。'), '新的旅程开始了。')
            chat.assert_awaited_once()

    async def test_foreign_tmdb_does_not_bypass_chinese_tvdb(self):
        with patch.object(fallback, '_try_tmdb_zh', AsyncMock(return_value='彼は出発した。')), \
             patch.object(fallback, '_try_tvdb_zh', AsyncMock(return_value='他出发了。')):
            self.assertEqual(await fallback.resolve_episode_plot(
                tmdb_id=1, tmdb_season=1, tmdb_ep_num=1,
                tvdb_id=1, tvdb_season=1, tvdb_ep=1), '他出发了。')

    async def test_foreign_metadata_translated_when_no_other_source(self):
        with patch.object(fallback, '_try_tmdb_zh', AsyncMock(return_value='He leaves.')), \
             patch.object(fallback, 'translate_ja_to_zh', AsyncMock(return_value='他出发了。')) as chat:
            self.assertEqual(await fallback.resolve_episode_plot(
                tmdb_id=1, tmdb_season=1, tmdb_ep_num=1), '他出发了。')
            chat.assert_awaited_once_with('He leaves.')

    async def test_plot_log_shows_fallback_order_and_final_source(self):
        with patch.object(fallback, '_try_tmdb_zh', AsyncMock(return_value='He leaves.')), \
             patch.object(fallback, '_try_tvdb_zh', AsyncMock(return_value='他出发了。')), \
             self.assertLogs(fallback.logger, level='INFO') as logs:
            result = await fallback.resolve_episode_plot(
                tmdb_id=1, tmdb_season=0, tmdb_ep_num=1,
                tvdb_id=2, tvdb_season=0, tvdb_ep=1,
                context='S00E01')
        self.assertEqual(result, '他出发了。')
        messages = '\n'.join(logs.output)
        self.assertLess(messages.index('TMDB zh-CN：非中文'),
                        messages.index('TVDB zho：命中中文简介'))
        self.assertIn('S00E01 简介', messages)

    async def test_title_log_reports_actual_fallback_source(self):
        selected = []
        with patch.object(translate, 'chat', AsyncMock(return_value='')), \
             self.assertLogs(translate.logger, level='INFO') as logs:
            result = await translate.resolve_episode_title(
                '', '日本語', 'TMDB 标题', context='S01E02',
                selected_source=selected)
        self.assertEqual(result, 'TMDB 标题')
        self.assertEqual(selected, ['TMDB'])
        self.assertIn('最终使用：TMDB', '\n'.join(logs.output))

    def test_script_validation(self):
        for text in ['', '123...', 'He leaves.', '他出发了。あ', '他出发了。ｱ']:
            self.assertFalse(translate.is_chinese_plot(text))
        self.assertTrue(translate.is_chinese_plot('他出发了。'))


if __name__ == '__main__':
    unittest.main()
