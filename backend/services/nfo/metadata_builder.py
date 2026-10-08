"""Metadata orchestration — generate all NFO layers for a single episode download.

Called after a torrent is added to qBittorrent but before it is resumed.
Adapts older RSS calls through the RSS matcher, then projects canonical mappings for
:func:`batch_nfo_generator`, delegates all NFO + image generation to it,
then renames the file in qBittorrent.
"""

import logging
from pathlib import Path

from ... import config
from ...clients.qbittorrent import rename_file
from ...data import get_all_episodes

from .generator import batch_nfo_generator

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
    tvdb_ep: int | None = None,
    season_dir: str = "",
    show_dir: str = "",
    bgm_subject_name: str = "",
    series_name: str = "",
    rename_in_qbit: bool = True,
    overwrite: bool = False,
    metadata_ctx=None,
    base_path: str | None = None,
    episode_mapping=None,
) -> bool:
    """Generate NFO + images via :func:`batch_nfo_generator`, then rename in qBittorrent.

    All NFO XML writing, image downloading, and metadata fetching is
    delegated to the shared batch function.  This function only handles
    RSS-specific concerns: compatibility input adaptation,
    and the final qBittorrent rename.

    ``rename_in_qbit=False`` skips the rename entirely (NFO-only
    regeneration, e.g. when the torrent may already be removed).  In that
    case ``qb_client``, ``info_hash`` and ``old_torrent_path`` are unused.

    ``overwrite=True`` rewrites the episode NFO + thumb even when they
    already exist (used together with ``rename_in_qbit=False`` by regen).
    """
    from ...domain.episode_metadata_adapters import mapping_to_legacy_batch_episode
    from ...domain.rss_episode import rss_episode_ref
    from ..rss_episode_matcher import subscription_episode_mapping
    from .metadata_context import MetadataContext
    metadata_ctx = metadata_ctx or MetadataContext()
    if episode_mapping is None:
        # Compatibility entry for regeneration and older direct callers. All
        # identity decisions still occur in the RSS matcher, before NFO work.
        overrides = get_all_episodes(bangumi_id).get(str(sort), {})
        sub = {"bgm": {"season": bgm_season},
               "tmdb": {"id": tmdb_id, "season": tmdb_season, "ep_offset": tmdb_ep_offset},
               "tvdb": {"id": tvdb_id, "season": tvdb_season, "ep_offset": tvdb_ep_offset}}
        episode_mapping = await subscription_episode_mapping(
            rss_episode_ref({}, ""), sub, bangumi_id, metadata_ctx,
            sort=sort, overrides=overrides, tvdb_episode_number=tvdb_ep,
        )
    pre_path = base_path if base_path is not None else str(Path(show_dir).parent)
    nfo_episodes = [mapping_to_legacy_batch_episode(episode_mapping)]

    # ── Delegate to shared NFO + image pipeline ─────────────────────
    summary = await batch_nfo_generator(
        pre_path, nfo_episodes, series_name=series_name, overwrite=overwrite,
        metadata_ctx=metadata_ctx,
    )
    if summary.get("episodesProcessed", summary.get("nfoGenerated", 0)) == 0:
        logger.error("NFO generation produced no output")
        return False
    logger.info("batch NFO complete: %s", summary)

    # ── Rename in qBittorrent (skipped for NFO-only regeneration) ────
    if rename_in_qbit:
        ext = Path(old_torrent_path).suffix
        new_path = summary["episodePaths"][0] + ext
        try:
            renamed = await rename_file(qb_client, info_hash, old_torrent_path, new_path)
            if renamed is False:
                return False
            logger.info("renamed: %s → %s", old_torrent_path, new_path)
        except Exception:
            logger.exception("rename failed")
            return False

    return True
