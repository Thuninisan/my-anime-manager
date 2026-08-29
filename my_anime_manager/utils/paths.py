"""Shared filesystem paths."""

from pathlib import Path

# Subtitle upload storage. Historically resolved against the api package
# directory (my_anime_manager/api/data/subtitles) — keep the same location
# so existing uploads stay visible.
SUBTITLE_DIR = Path(__file__).parent.parent / "api" / "data" / "subtitles"
