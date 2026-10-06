"""Confirmed subtitle destinations must agree before and after monitor recovery."""
import unittest
from unittest.mock import patch

from backend.services.torrent.monitor import build_processing, _subtitle_suffix


class TorrentSubtitleTests(unittest.TestCase):
    def test_multiple_subtitles_keep_distinct_destinations(self):
        context = {
            "hardlink_root": "/library", "torrent_name": "Show", "movie_meta": {"tmdb_name": "Movie"},
            "files": [
                {"torrent_path": "folder/video.mkv"},
                {"torrent_path": "folder/video.chs.ass", "is_subtitle": True, "subtitle_suffix": ".sub1.ass"},
            ],
            "uploaded_subtitles": [{"stored_filename": "uploaded.ass", "subtitle_suffix": ".sub2.ass"}],
        }
        result = build_processing(context)
        self.assertEqual([f["target_path"] for f in result["files"]], [
            "/library/Movie/Movie.mkv", "/library/Movie/Movie.sub1.ass", "/library/Movie/Movie.sub2.ass",
        ])
        self.assertEqual(result["files"][1]["torrent_path"], "folder/video.chs.ass")

    def test_tv_destination_and_invalid_suffix(self):
        self.assertEqual(_subtitle_suffix({"subtitle_suffix": "../../evil.ass"}, ".ass"), ".ass")
        context = {
            "hardlink_root": "/library", "torrent_name": "Show", "files": [
                {"torrent_path": "subs/video.ass", "is_subtitle": True, "subtitle_suffix": ".sub2.ass"},
            ],
        }
        with patch('backend.services.nfo.format_download_path', return_value='Show/Season 1/Show S01E01.mkv'):
            result = build_processing(context)
        self.assertEqual(result["files"][0]["target_path"], '/library/Show/Season 1/Show S01E01.sub2.ass')


if __name__ == '__main__':
    unittest.main()
