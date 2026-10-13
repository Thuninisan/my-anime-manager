"""Resolve episode output paths, preferring the recorded media destination."""

from pathlib import Path

from fastapi import HTTPException

from ... import config
from ...data import list_subscriptions, get_all_episodes
from ..nfo import format_download_path


def resolve_episode_paths(bangumi_id: int, sort: int) -> tuple[dict, str, str]:
    """Resolve the subscription and download dirs for one episode.

    Shared by the download-history routes (upload/replace) and NFO
    regeneration.  Returns ``(sub, season_dir, show_dir)``.  Raises
    HTTPException(404) when the subscription does not exist.
    """
    subs = list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if not sub:
        raise HTTPException(404, "订阅不存在")

    download_path = get_all_episodes(bangumi_id).get(str(sort), {}).get("download_path")
    if download_path:
        season_dir = str(Path(download_path).parent)
        return sub, season_dir, str(Path(season_dir).parent)
    # TVDB 季内集数 = Bangumi sort + 平台偏移；未知仅在本次路径计算中回退为 0。
    tvdb_ep_val = sort + (sub.get("tvdb", {}).get("ep_offset") or 0)
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
    rel_path = format_download_path(
        config.RSS_PATH_TEMPLATE, sub, sort=sort, tvdb_episode=tvdb_ep_val,
    ).lstrip("/")
    rel_dir = str(Path(rel_path).parent)
    season_dir = str(Path(rss_base) / rel_dir)
    show_dir = str(Path(season_dir).parent)
    return sub, season_dir, show_dir
