"""Shared filesystem paths for application data and uploads."""

import os
from pathlib import Path

PACKAGE_DATA_DIR = Path(__file__).parent.parent / "data"
USER_DATA_DIR = Path(os.environ["MAM_DATA_DIR"]) if os.environ.get("MAM_DATA_DIR") else PACKAGE_DATA_DIR

# Subtitle upload storage. Historically resolved against the api package
# directory (backend/api/data/subtitles) — keep the same location
# so existing uploads stay visible.
SUBTITLE_DIR = Path(__file__).parent.parent / "api" / "data" / "subtitles"
