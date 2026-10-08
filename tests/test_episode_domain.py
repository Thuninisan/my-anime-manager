"""Episode domain boundary and preview compatibility regressions."""
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from backend.domain.episode import create_episode_mapping
from backend.domain.episode_adapters import episode_catalog, merged_episode_metadata, parsed_episode_ref
from backend.services.torrent import search


class EpisodeDomainTests(unittest.TestCase):
    def mapping(self, parsed_episode=3, tmdb_season=1, tmdb_episode=3, tvdb_episode=3, absolute=3, season=1):
        return create_episode_mapping(
            parsed_episode_ref({"season": season, "episode": parsed_episode}),
            {"subject_id": 200, "episode_id": 201, "episode_number": 3, "episode_absolute": absolute},
            {"series_id": 100, "episode_id": 101, "season_number": tmdb_season, "episode_number": tmdb_episode},
            {"series_id": 300, "episode_id": 301, "season_number": season, "episode_number": tvdb_episode},
            "tmdb",
        )

    def test_regular_mapping_is_json_serializable(self):
        mapping = self.mapping()
        self.assertEqual(json.loads(json.dumps(mapping)), mapping)
        self.assertEqual(mapping["parsed"], {"season_number": 1, "episode_number": 3})
        self.assertEqual(mapping["bangumi"]["episode_absolute"], 3)
        self.assertEqual(mapping["tmdb"]["episode_number"], 3)
        self.assertEqual(mapping["tvdb"]["episode_number"], 3)

    def test_different_provider_numbering_is_independent(self):
        mapping = self.mapping(parsed_episode=15, tmdb_season=2, tmdb_episode=3, tvdb_episode=15, absolute=15)
        self.assertEqual(mapping["parsed"]["episode_number"], 15)
        self.assertEqual((mapping["tmdb"]["season_number"], mapping["tmdb"]["episode_number"]), (2, 3))
        self.assertEqual((mapping["tvdb"]["season_number"], mapping["tvdb"]["episode_number"]), (1, 15))
        self.assertEqual(mapping["bangumi"]["episode_absolute"], 15)

    def test_specials_preserve_zero(self):
        mapping = self.mapping(season=0, tmdb_season=0)
        for provider in ("parsed", "tmdb", "tvdb"):
            self.assertEqual(mapping[provider]["season_number"], 0)

    def test_missing_provider_uses_nulls(self):
        mapping = create_episode_mapping({"season_number": None, "episode_number": 3})
        self.assertEqual(mapping["tvdb"], {
            "series_id": None, "episode_id": None, "season_number": None, "episode_number": None,
        })
        self.assertIsNone(mapping["match_source"])

    def test_catalog_normalizes_ids_numbers_and_keeps_ep_sort_independent(self):
        raw = {
            "tmdb": {"100": {"0": {"name": "Specials", "episodes": [{"epNum": 3, "tmdbId": 101, "name": "Title"}]}}},
            "tvdb": {"300": {"name": "Show", "seasons": {"1": {"name": "Season", "episodes": [{"epNum": 15, "tvdbId": 301, "absoluteNumber": 15}]}}}},
            "bangumi": {"200": {"name": "Show", "episodes": [{"id": 201, "ep": 2.5, "sort": 15, "raw_sort": 15}]}},
        }
        before = copy.deepcopy(raw)
        catalog = episode_catalog(raw)
        self.assertEqual(raw, before)  # downstream NFO data is untouched
        bgm = catalog["bangumi"]["200"]["episodes"][0]
        self.assertEqual((bgm["episode_number"], bgm["episode_absolute"]), (2.5, 15))
        self.assertEqual(catalog["tmdb"]["100"]["0"]["episodes"][0]["season_number"], 0)
        self.assertEqual(catalog["tvdb"]["300"]["seasons"]["1"]["episodes"][0]["episode_id"], 301)
        encoded = json.dumps(catalog)
        for alias in ('epNum', 'tmdbId', 'tvdbId', 'absoluteNumber', 'raw_sort'):
            self.assertNotIn(alias, encoded)

    def test_bangumi_missing_sort_and_zero_sort_do_not_use_ep(self):
        for raw_sort in (None, 0):
            catalog = episode_catalog({"bangumi": {"200": {"episodes": [{
                "id": 201, "ep": 9, "sort": 9, "raw_sort": raw_sort,
            }]}}})
            bgm = catalog["bangumi"]["200"]["episodes"][0]
            self.assertEqual(bgm["episode_number"], 9)
            self.assertEqual(bgm["episode_absolute"], raw_sort)

    def test_merged_metadata_is_separate_and_does_not_guess_provenance(self):
        raw = {"name": "Title", "overview": "Plot", "airDate": "2026-01-01", "runtime": 24,
               "still_path": "https://image.example/1.jpg", "voteAverage": 8.0,
               "guestStars": [{"name": "Actor", "character": "Hero"}], "genres": ["Animation"]}
        metadata = merged_episode_metadata(raw)
        self.assertEqual(metadata["air_date"], "2026-01-01")
        self.assertEqual(metadata["rating"], 8.0)
        self.assertEqual(metadata["actors"], ["Actor"])
        self.assertEqual(metadata["runtime_minutes"], 24)
        self.assertNotIn("genres", metadata)
        self.assertTrue(all(value is None for value in metadata["sources"].values()))
        self.assertIsNone(merged_episode_metadata({})["plot"])
        self.assertIsNone(merged_episode_metadata({"stillPath": "/still.jpg"})["thumbnail_url"])
        self.assertEqual(merged_episode_metadata(
            {"stillPath": "/still.jpg"}, thumbnail_base_url="https://image.example/original/",
        )["thumbnail_url"], "https://image.example/original/still.jpg")


class BangumiPreviewBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_keeps_original_ep_sort_without_additional_requests(self):
        episodes = [{"id": 201, "ep": 2.5, "sort": 15, "name": "Title", "type": 0},
                    {"id": 202, "ep": 9, "sort": 0, "name": "Special", "type": 1}]
        with patch.object(search.bgm_client, "get_subject", new_callable=AsyncMock, return_value={"name": "Show"}) as subject, \
             patch.object(search.bgm_client, "get_episodes", new_callable=AsyncMock, return_value=episodes) as fetch:
            result = await search._fetch_bangumi_episodes(200)
        subject.assert_awaited_once_with(200)
        fetch.assert_awaited_once_with(200, ep_type=None)
        catalog = episode_catalog({"bangumi": {"200": result}})
        by_id = {ep["episode_id"]: ep for ep in catalog["bangumi"]["200"]["episodes"]}
        self.assertEqual((by_id[201]["episode_number"], by_id[201]["episode_absolute"]), (2.5, 15))
        self.assertEqual(by_id[202]["episode_absolute"], 0)
        # Keep existing fallback/sort ordering in the legacy API used by NFO.
        self.assertEqual(result["episodes"][0]["sort"], 9)
