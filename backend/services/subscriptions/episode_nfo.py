"""Regenerate one downloaded episode's metadata from its historical mapping.

The API retains ownership of the shared NFO write lock and conflict checks.
This workflow never renames files in qBittorrent.
"""

import logging
from pathlib import Path

from fastapi import HTTPException

from ... import config
from ...data import get_all_episodes
from ..nfo import generate_metadata
from .paths import resolve_episode_paths

logger = logging.getLogger(__name__)


async def regen_episode_nfo(bangumi_id: int, sort: int) -> None:
    """Regenerate NFO using relational mapping and the recorded media path.

    The stored episode mapping and media destination identify the output.
    Callers only need to identify the episode.
    NFO-only: rewrites NFO + images on disk and never touches qBittorrent
    (no rename), so it works even after the torrent has been removed.
    Raises HTTPException(4xx) on missing data, HTTPException(500) on failure.
    """
    sub, season_dir, show_dir = resolve_episode_paths(bangumi_id, sort)
    ep = get_all_episodes(bangumi_id).get(str(sort))
    if not ep:
        raise HTTPException(404, "该集的下载记录不存在")

    stored_mapping = ep.get("mapping")
    if stored_mapping is None or not any(stored_mapping.get(f"{p}_series_id") for p in ("tmdb", "tvdb")):
        raise HTTPException(409, "episode_mapping_unresolved: 下载记录缺少作品映射")
    from ...db.download_episode_mapping import episode_mapping
    historical_mapping = episode_mapping(bangumi_id, sort, stored_mapping)
    series_name = sub.get("series_name") or sub.get("name", str(bangumi_id))

    # qb_client / info_hash / old_torrent_path are only consumed by the
    # rename step, which regen skips (rename_in_qbit=False) — placeholders.
    ok = await generate_metadata(
        None,
        "",
        "",
        season_dir=season_dir,
        show_dir=show_dir,
        series_name=series_name,
        episode_mapping=historical_mapping,
        rename_in_qbit=False,
        overwrite=True,
        base_path=Path(ep["download_path"]).anchor if ep.get("download_path") else (config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH),
        download_path=ep.get("download_path"),
    )
    if not ok:
        raise HTTPException(500, "NFO 生成失败")
    logger.info("regen-nfo: NFO regenerated for bangumi=%d sort=%d", bangumi_id, sort)
