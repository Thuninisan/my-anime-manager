"""Metadata orchestration — generate all NFO layers for a single episode download.

Called after a torrent is added to qBittorrent but before it is resumed.
Normalises the scattered RSS parameters into the format expected by
:func:`batch_nfo_generator`, delegates all NFO + image generation to it,
then renames the file in qBittorrent.
"""

import logging
from pathlib import Path

from ... import config
from ...clients.qbittorrent import rename_file
from ...data import get_all_episodes

from .generator import batch_nfo_generator, format_download_path

logger = logging.getLogger(__name__)


async def generate_metadata(
    qb_client, info_hash: str,
    bangumi_id: int, sort: int, bgm_subject_id: int,
    tmdb_id: int, show_name: str, old_torrent_path: str, guid: str,
    bgm_season: int = 1,
    tmdb_season: int | None = None,
    tmdb_ep_offset: int = 0,
    tvdb_id: int = 0,
    tvdb_season: int | None = None,
    tvdb_ep_offset: int = 0,
    tvdb_ep: int = 0,
    season_dir: str = "",
    show_dir: str = "",
    bgm_subject_name: str = "",
    series_name: str = "",
) -> bool:
    """Generate NFO + images via :func:`batch_nfo_generator`, then rename in qBittorrent.

    All NFO XML writing, image downloading, and metadata fetching is
    delegated to the shared batch function.  This function only handles
    RSS-specific concerns: download-history overrides, input normalisation,
    and the final qBittorrent rename.
    """
    # ── Apply download-history overrides ────────────────────────────
    overrides = get_all_episodes(bangumi_id).get(str(sort), {})
    eff_tmdb_season = tmdb_season or bgm_season
    eff_tmdb_ep = sort + (tmdb_ep_offset or 0)
    if overrides.get("tmdb_season") is not None:
        eff_tmdb_season = overrides["tmdb_season"]
    if overrides.get("tmdb_ep") is not None:
        eff_tmdb_ep = overrides["tmdb_ep"]

    eff_tvdb_season = tvdb_season or bgm_season
    tvdb_ep_val = tvdb_ep or 1

    # ── Normalise to batch_nfo_generator format ─────────────────────
    pre_path = str(Path(show_dir).parent)
    nfo_episodes = [{
        "bangumi_subject_id": bangumi_id,
        "bangumi_episode_sort": sort,
        "tvdb_id": tvdb_id,
        "tvdb_season": eff_tvdb_season,
        "tvdb_episode": tvdb_ep_val,
        "tmdb_id": tmdb_id,
        "tmdb_season": eff_tmdb_season,
        "tmdb_episode": eff_tmdb_ep,
    }]

    # ── Delegate to shared NFO + image pipeline ─────────────────────
    summary = await batch_nfo_generator(pre_path, nfo_episodes, series_name=series_name)
    if summary.get("nfoGenerated", 0) == 0:
        logger.error("NFO generation produced no output")
        return False
    logger.info("batch NFO complete: %s", summary)

    # ── Rename in qBittorrent ───────────────────────────────────────
    ext = Path(old_torrent_path).suffix
    _stem_sub = {
        "name": show_name,
        "series_name": series_name or show_name,
        "bgm": {
            "subject_name": bgm_subject_name or show_name,
            "season": bgm_season,
        },
        "tvdb": {"season": eff_tvdb_season},
        "tmdb": {"season": eff_tmdb_season},
    }
    new_path = format_download_path(
        config.RSS_PATH_TEMPLATE, _stem_sub, sort=sort, ext=ext,
        bangumi_sort=sort, bangumi_ep=sort,
        tvdb_episode=tvdb_ep_val, tmdb_episode=eff_tmdb_ep,
    ).lstrip("/")
    try:
        await rename_file(qb_client, info_hash, old_torrent_path, new_path)
        logger.info("renamed: %s → %s", old_torrent_path, new_path)
    except Exception:
        logger.exception("rename failed")

    return True
