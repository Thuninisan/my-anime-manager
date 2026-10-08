import unittest
from backend.domain.episode_adapters import episode_catalog
from backend.domain.rss_episode import rss_episode_ref
from backend.services.rss_episode_matcher import build_episode_mapping, normalize_episode_number


class RssCanonicalTests(unittest.TestCase):
    def test_input_and_offsets(self):
        self.assertIsNone(rss_episode_ref({}, "title")["rss_episode_number"])
        for raw, offset, expected in [(13, -12, 1), (1, 12, 13), (0, 0, 0)]:
            ref = rss_episode_ref({"episode_number": raw}, "title")
            self.assertEqual(normalize_episode_number(ref, offset), expected)
        self.assertIsNone(normalize_episode_number(rss_episode_ref({}, ""), 0))

    def test_coordinates_and_missing_fields(self):
        for bgm in [{"id": 8, "ep": 7, "sort": 0}, {"sort": 0}, {"ep": 7}]:
            for providers in [(True, True), (True, False), (False, True), (False, False)]:
                catalog = episode_catalog({"bangumi": {"1": {"episodes": [bgm]}},
                    "tmdb": {"2": {"0": {"episodes": [{"epNum": 0}]}}},
                    "tvdb": {"3": {"seasons": {"2": {"episodes": [{"epNum": 5, "tvdbId": 9}]}}}}})
                mapping = build_episode_mapping(rss_episode_ref({"episode_number": 12}, "irrelevant"), {
                    "bangumi_subject_id": 1, "rss_offset": -12,
                    "tmdb": {"series_id": 2 if providers[0] else None, "season_number": 0},
                    "tvdb": {"series_id": 3 if providers[1] else None, "season_number": 2, "episode_offset": 5}}, catalog)
                self.assertEqual(mapping["bangumi"]["episode_absolute"], 0)
                self.assertEqual(mapping["bangumi"]["episode_number"], bgm.get("ep") if "sort" in bgm else None)
                self.assertEqual(mapping["tmdb"]["season_number"], 0)
                self.assertIsNone(mapping["tmdb"]["episode_id"])
                self.assertNotIn("offset", mapping)
                self.assertNotIn("rss_episode_number", mapping)
                if providers[1]:
                    self.assertEqual(mapping["tvdb"]["episode_id"], 9)

    def test_multi_season_and_agreeing_provider_coordinates(self):
        for season in (0, 1, 4):
            catalog = episode_catalog({"tmdb": {"2": {str(season): {"episodes": [{"epNum": 5, "tmdbId": 20}]}}},
                "tvdb": {"3": {"seasons": {str(season): {"episodes": [{"epNum": 5, "tvdbId": 30}]}}}}})
            mapping = build_episode_mapping(rss_episode_ref({"episode_number": 17}, "title"), {
                "bangumi_subject_id": 1, "rss_offset": -12,
                "tmdb": {"series_id": 2, "season_number": season},
                "tvdb": {"series_id": 3, "season_number": season}}, catalog)
            for provider in ("tmdb", "tvdb"):
                self.assertEqual(mapping[provider]["season_number"], season)
                self.assertEqual(mapping[provider]["episode_number"], 5)

    def test_unknown_input_rejected(self):
        with self.assertRaises(ValueError):
            build_episode_mapping(rss_episode_ref({}, ""), {"bangumi_subject_id": 1},
                                  {"tmdb": {}, "tvdb": {}, "bangumi": {}})


class RssLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_reuse_and_override_before_downstream(self):
        from unittest.mock import AsyncMock, patch
        from backend.services.nfo.metadata_context import MetadataContext
        from backend.services.rss_episode_matcher import subscription_episode_mapping
        from backend.domain.episode_metadata_adapters import metadata_candidates_from_catalogs
        from backend.services.episode_metadata_resolver import resolve_episode
        ctx = MetadataContext()
        raw = {0: {"episodes": [{"epNum": 0, "tmdbId": 20, "voteAverage": 0, "name": "标题"}]}}
        ctx.bgm_episodes[1] = [{"id": 10, "sort": 0, "ep": 7, "name_cn": "标题"}]
        ctx.tmdb_season_maps[(2, "zh-CN")] = raw
        ctx.tvdb_series[(3, "jpn")] = {"seasons": {"2": {"episodes": [{"epNum": 9, "tvdbId": 30}]}}}
        sub = {"bgm": {"season": 1}, "tmdb": {"id": 2, "season": 1},
               "tvdb": {"id": 3, "season": 2, "ep_offset": 9}}
        with patch("backend.services.tmdb.build_season_episode_map", AsyncMock()) as tmdb_fetch, \
             patch("backend.services.tvdb.fetch_tvdb_series_episodes", AsyncMock()) as tvdb_fetch:
            mapping = await subscription_episode_mapping(rss_episode_ref({"episode_number": 12}, "unrelated"),
                sub, 1, ctx, sort=0, overrides={"tmdb_season": 0, "tmdb_ep": 0})
            again = await subscription_episode_mapping(rss_episode_ref({}, ""), sub, 1, ctx, sort=0,
                overrides={"tmdb_season": 0, "tmdb_ep": 0})
            self.assertEqual(mapping, again)
            self.assertEqual(mapping["tmdb"]["episode_id"], 20)
            available = metadata_candidates_from_catalogs(mapping,
                await ctx.get_tmdb_season_map(2, "zh-CN"), await ctx.get_tvdb_series(3), await ctx.get_bgm_episodes(1))
            resolved = resolve_episode(mapping, available)
            self.assertEqual((resolved["season_number"], resolved["episode_number"]), (2, 9))
            self.assertEqual(resolved["metadata"]["rating"], 0)
            tmdb_fetch.assert_not_awaited()
            tvdb_fetch.assert_not_awaited()
            self.assertIs(ctx.tmdb_season_maps[(2, "zh-CN")], raw)

    async def test_rss_mapping_matches_phase2_goldens(self):
        import tempfile
        import xml.etree.ElementTree as ET
        from pathlib import Path
        from tests.test_episode_metadata import candidates
        from backend.services.episode_metadata_resolver import resolve_episode
        from backend.services.nfo.nfo_xml import generate_episode_nfo
        # Same metadata fixtures as Phase 2, but identity is supplied by RSS rules.
        cases = {"regular": (1, 3), "special": (0, 3), "missing_tmdb": (1, 3),
                 "missing_tvdb": (1, 3), "bangumi_only": (1, 3), "manual_override": (2, 15)}
        for case, (season, number) in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                catalog = episode_catalog({"bangumi": {"200": {"episodes": [{"id": 201, "sort": 0, "ep": 2.5}]}},
                    "tmdb": {"100": {"1": {"episodes": [{"epNum": 3, "tmdbId": 101}]}}},
                    "tvdb": {"300": {"seasons": {str(season): {"episodes": [{"epNum": number, "tvdbId": 301}]}}}}})
                ref = build_episode_mapping(rss_episode_ref({"episode_number": 12}, ""), {
                    "bangumi_subject_id": 200, "rss_offset": -12,
                    "tmdb": {"series_id": 100, "season_number": 1, "episode_offset": 3},
                    "tvdb": {"series_id": 300, "season_number": season, "episode_offset": number}}, catalog)
                available = candidates()
                available["tmdb"]["still_path"] = "episode.jpg"
                if case in ("missing_tmdb", "bangumi_only"):
                    available["tmdb"] = None
                if case in ("missing_tvdb", "bangumi_only"):
                    available["tvdb"] = None
                    ref["tvdb"]["episode_id"] = 0
                if available["tvdb"] and available["tmdb"]:
                    available["tvdb"]["rating"] = 8.2
                resolved = resolve_episode(ref, available)
                if case == "bangumi_only":
                    resolved["metadata"]["plot"] = "翻译后的简介。"
                written = generate_episode_nfo(resolved, show_name="测试番剧 & Show", bangumi_subject_name="作品原名",
                    thumb_path="" if case == "bangumi_only" else "episode.jpg", output_dir=tmp, file_stem="episode")
                expected = Path(__file__).parent / "fixtures" / "episode_nfo" / f"{case}.xml"
                self.assertEqual(ET.canonicalize(Path(written).read_text(), strip_text=True),
                                 ET.canonicalize(expected.read_text(), strip_text=True))
