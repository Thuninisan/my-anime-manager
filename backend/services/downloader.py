"""Compatibility entry points for the split RSS downloader.

Repository callers use subscriptions/downloads directly. These aliases retain
public call signatures and share the exact same worker state and lock. Private
helpers and imported implementation details are not compatibility APIs.
"""

from .subscriptions.worker import (
    get_status, get_config, start, stop, set_interval, apply_interval,
    run_once, poll_subscription,
)
from .downloads.episode_submission import (
    check_qbit, submit_episode_torrent, VIDEO_EXTENSIONS,
)
from .subscriptions.paths import resolve_episode_paths
from .subscriptions.episode_nfo import regen_episode_nfo
from .enrich import enrich_subscription

__all__ = [
    "get_status", "get_config", "start", "stop", "set_interval", "apply_interval",
    "run_once", "poll_subscription", "check_qbit", "submit_episode_torrent",
    "VIDEO_EXTENSIONS", "resolve_episode_paths", "regen_episode_nfo",
    "enrich_subscription",
]
