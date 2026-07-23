"""Shared state variables used across api/ route modules.

Avoids circular imports between __init__.py and route files.
"""

import asyncio
from pathlib import Path
from typing import Optional

# ── Scan ──
_scan_task: Optional[asyncio.Task] = None
_scan_status: dict = {
    "running": False,
    "dir": "",
    "total": 0,
    "processed": 0,
    "deleted": 0,
    "failed": 0,
    "current_file": "",
    "errors": [],
}

# ── Watch ──
_watch_task: Optional[asyncio.Task] = None
_watch_status: dict = {
    "running": False,
    "dir": "",
    "processed": 0,
    "failed": 0,
    "errors": [],
}

# ── Download monitor ──
_download_tasks: dict[str, asyncio.Task] = {}

# ── Update ──
_update_cache: dict = {"checked_at": None, "result": None}
_SOURCE_DIR: str = ""
