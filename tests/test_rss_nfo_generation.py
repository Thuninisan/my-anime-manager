"""RSS metadata generation must finish after writing NFO files."""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend import config
from backend.services.nfo import metadata_builder
from backend.services.nfo.metadata_context import MetadataContext


class RssNfoGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_returns_success_and_renames_after_writing_nfo(self):
        ctx = MetadataContext()
        ctx.bgm_subjects[789] = {"name_cn": "测试番剧"}
        ctx.bgm_episodes[789] = [
            {"id": 790, "sort": 1, "ep": 1, "name_cn": "第一集"},
        ]
        ctx.tmdb_details[(123, "zh-CN")] = {
            "name": "测试番剧", "overview": "这是番剧简介。",
        }
        season_map = {1: {"episodes": [
            {"epNum": 1, "name": "第一集", "overview": "这是本集简介。"},
        ]}}
        ctx.tmdb_season_maps[(123, "zh-CN")] = season_map
        template = "{series_name}/Season {tmdb_season:02d}/{bangumi_title} {tmdb_episode:02d}"
        qb = object()

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(config, "RSS_PATH_TEMPLATE", template), \
             patch.object(metadata_builder, "get_all_episodes", return_value={}), \
             patch.object(metadata_builder, "rename_file", AsyncMock()) as rename, \
             patch("backend.services.nfo.images.download_show_images", AsyncMock(return_value={})):
            success = await metadata_builder.generate_metadata(
                qb, "test-hash", 789, 1, 789, 123, "测试番剧",
                "original.mkv", "test-guid", tmdb_season=1,
                show_dir=str(Path(tmp) / "测试番剧"), metadata_ctx=ctx,
            )

            self.assertTrue(success)
            rename.assert_awaited_once_with(
                qb, "test-hash", "original.mkv", "测试番剧/Season 01/测试番剧 01.mkv",
            )
            show_dir = Path(tmp) / "测试番剧"
            self.assertTrue((show_dir / "tvshow.nfo").is_file())
            self.assertTrue((show_dir / "Season 01" / "season.nfo").is_file())
            episode = ET.parse(show_dir / "Season 01" / "测试番剧 01.nfo").getroot()
            self.assertEqual(episode.findtext("title"), "第一集")
            self.assertEqual(episode.findtext("season"), "1")
            self.assertEqual(episode.findtext("episode"), "1")
            # Shared poll metadata remains available to subsequent RSS entries.
            self.assertIs(ctx.tmdb_season_maps[(123, "zh-CN")], season_map)
