"""Metadata orchestration — generate all NFO layers for a single episode download.

Called after a torrent is added to qBittorrent but before it is resumed.
Passes canonical mappings to :func:`batch_nfo_generator`, delegates all NFO + image generation to it,
then renames the file in qBittorrent.
"""

import logging
from pathlib import Path

from ...clients.qbittorrent import rename_file

from .generator import batch_nfo_generator

logger = logging.getLogger(__name__)


async def generate_metadata(
    qb_client, info_hash: str, old_torrent_path: str, *, episode_mapping,
    season_dir: str = "", show_dir: str = "", series_name: str = "",
    rename_in_qbit: bool = True, overwrite: bool = False, metadata_ctx=None,
    base_path: str | None = None, processing_result=None,
) -> bool:
    """Generate NFO + images via :func:`batch_nfo_generator`, then rename in qBittorrent.

    All NFO XML writing, image downloading, and metadata fetching is
    delegated to the shared batch function.  This function only handles
    RSS-specific output paths and the final qBittorrent rename.

    ``rename_in_qbit=False`` skips the rename entirely (NFO-only
    regeneration, e.g. when the torrent may already be removed).  In that
    case ``qb_client``, ``info_hash`` and ``old_torrent_path`` are unused.

    ``overwrite=True`` rewrites the episode NFO + thumb even when they
    already exist (used together with ``rename_in_qbit=False`` by regen).
    """
    from .metadata_context import MetadataContext
    metadata_ctx = metadata_ctx or MetadataContext()
    if episode_mapping is None:
        raise ValueError("canonical_episode_mapping_required")
    pre_path = base_path if base_path is not None else str(Path(show_dir).parent)
    nfo_episodes = [{"episode_mapping": episode_mapping}]

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
            if processing_result is not None:
                processing_result.update(source_path=old_torrent_path,
                                         target_path=str(Path(pre_path) / new_path),
                                         season_dir=season_dir, show_dir=show_dir)
            logger.info("renamed: %s → %s", old_torrent_path, new_path)
        except Exception:
            logger.exception("rename failed")
            return False

    return True
