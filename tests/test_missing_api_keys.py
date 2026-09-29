"""Missing metadata API credentials fail before sending HTTP requests."""

import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.clients import tmdb, tvdb
from backend.clients.errors import MissingAPIKeyError


class MissingAPIKeyTests(unittest.TestCase):
    def test_tmdb_empty_or_whitespace_key(self):
        for key in ("", "  "):
            with self.subTest(key=key), patch.object(tmdb.config, "TMDB_API_KEY", key), \
                    patch.object(tmdb, "fetch_with_retry", new_callable=AsyncMock) as fetch:
                with self.assertRaisesRegex(MissingAPIKeyError, "TMDB_API_KEY"):
                    asyncio.run(tmdb.search_tv("example"))
                fetch.assert_not_awaited()

    def test_tvdb_empty_key_even_with_cached_token(self):
        with patch.object(tvdb.config, "TVDB_API_KEY", "  "), \
                patch.object(tvdb, "_token", "cached"), \
                patch.object(tvdb, "fetch_with_retry", new_callable=AsyncMock) as fetch:
            with self.assertRaisesRegex(MissingAPIKeyError, "TVDB_API_KEY"):
                asyncio.run(tvdb.search_series("example"))
            fetch.assert_not_awaited()

    def test_api_returns_readable_error(self):
        with patch("backend.api.routes_resources.resources.get_resource", return_value={
            "torrent_path": "/tmp/example.torrent", "source": "example", "source_id": "1"
        }), patch("backend.api.routes_resources.resources.torrent_file_path", return_value=Path("/tmp/example.torrent")), \
                patch("backend.api.routes_resources.Path.is_file", return_value=True), \
                patch("backend.services.torrent.preview.parse_and_search", new_callable=AsyncMock,
                      side_effect=MissingAPIKeyError("TMDB_API_KEY")):
            response = TestClient(app).post("/api/resources/1/torrent-preview")
        self.assertEqual(response.status_code, 503)
        self.assertIn("TMDB_API_KEY", response.json()["detail"])
