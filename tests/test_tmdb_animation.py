"""Offline regression coverage for animation-only automatic matching."""
import ast
import importlib.util
import logging
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
client = types.SimpleNamespace(search_tv=AsyncMock(), search_movie=AsyncMock())
clients = types.ModuleType("backend.clients")
clients.tmdb = client
spec = importlib.util.spec_from_file_location(
    "backend.services.tmdb", ROOT / "backend/services/tmdb.py")
tmdb = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"backend.clients": clients}):
    spec.loader.exec_module(tmdb)

# Avoid loading torrent workers and external clients for these search-only tests.
path = ROOT / "backend/services/torrent/search.py"
tree = ast.parse(path.read_text())
helpers = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
           and n.name in ("_search_tmdb_single", "_search_tmdb_movie")]
from backend.services.resource_resolver import select_provider_result
scope = dict(select_provider_result=select_provider_result, tmdb_client=client, tmdb_service=tmdb, logger=logging.getLogger("test.search"))
exec(compile(ast.Module(body=helpers, type_ignores=[]), str(path), "exec"), scope)


def candidate(id, genres):
    return dict(id=id, name="Yuru Camp", title="Yuru Camp",
                genre_ids=genres, original_name="ゆるキャン", original_title="ゆるキャン")


class AnimationTests(unittest.IsolatedAsyncioTestCase):
    async def search_all(self, results):
        response = types.SimpleNamespace(json=lambda: {"results": results})
        client.search_tv.return_value = response
        client.search_movie.return_value = response
        return [
            await scope["_search_tmdb_single"]("Yuru Camp"),
            await scope["_search_tmdb_movie"]("Yuru Camp"),
            await tmdb.search_tv_show("Yuru Camp"),
        ]

    async def test_live_action_first_is_excluded(self):
        for result in await self.search_all([candidate(1, [18]), candidate(2, [16, 18])]):
            self.assertEqual(result["id"], 2)

    async def test_single_live_action_is_unmatched(self):
        self.assertEqual(await self.search_all([candidate(1, [18])]), [None] * 3)

    async def test_multiple_live_action_results_do_not_fall_back(self):
        self.assertEqual(await self.search_all([candidate(1, [18]), candidate(2, [35])]), [None] * 3)

    async def test_single_animation_is_accepted(self):
        for result in await self.search_all([candidate(2, [16])]):
            self.assertEqual(result["id"], 2)

    async def test_missing_empty_and_null_genres_are_unmatched(self):
        for item in [{"id": 1}, candidate(1, []), candidate(1, None)]:
            self.assertEqual(await self.search_all([item]), [None] * 3)

    async def test_empty_search_is_unmatched(self):
        self.assertEqual(await self.search_all([]), [None] * 3)

    def test_filter_preserves_order_and_logs_reason(self):
        with self.assertLogs(tmdb.logger, level="INFO") as logs:
            result = tmdb.filter_animation_candidates(
                [candidate(1, [18]), candidate(3, [16]), candidate(2, [16, 35])], "Yuru Camp")
        self.assertEqual([r["id"] for r in result], [3, 2])
        self.assertIn("reason=missing_animation_genre", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
