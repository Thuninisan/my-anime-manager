"""Submit one subscription episode: mapping, paused add, metadata, resume, history.

RSS and manual uploads share this commit sequence. It does not schedule polls or
choose RSS candidates; callers own their existing replacement and retry policy.
"""

import logging
from pathlib import Path

from fastapi import HTTPException

from ... import config
from ...clients.qbittorrent import (
    login as qb_login, add_torrent, resume_torrent, delete_torrent, get_torrent_files,
)
from ...data import get_all_episodes, mark_downloaded, reset_fail_count
from ...utils.torrent_hash import compute_info_hash
from .episode_metadata import generate_metadata
from ..media.paths import format_download_path
from ..metadata.context import MetadataContext

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".mka", ".avi", ".mov", ".ts", ".wmv", ".flv", ".webm"}


async def submit_episode_torrent(
    torrent_path: str, bangumi_id: int, sort: int, source: str, *,
    sub: dict, guid: str, pub_date: str = "",
    metadata_ctx: MetadataContext | None = None, replace_existing: bool = False,
    rss_ref=None,
) -> str:
    """Shared RSS/upload commit: paused add, metadata/rename, resume, history."""
    tmdb = sub.get("tmdb", {})
    tvdb = sub.get("tvdb", {})
    from ...domain.resource_adapters import subscription_identity
    identity = subscription_identity(sub, bangumi_id)
    tmdb_id = identity["tmdb_series_id"] or 0
    tvdb_id = identity["tvdb_series_id"] or 0
    if not tmdb_id and not tvdb_id:
        raise HTTPException(400, "订阅未关联 TMDB 或 TVDB ID，无法生成 NFO")
    torrent_hash = compute_info_hash(torrent_path)
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
    from ...domain.rss_episode import rss_episode_ref
    from ..subscriptions.episode_mapping import subscription_episode_mapping
    metadata_ctx = metadata_ctx or MetadataContext()
    old = get_all_episodes(bangumi_id).get(str(sort), {})
    mapping_sub = {**sub, "tmdb": {**tmdb, "id": tmdb_id}, "tvdb": {**tvdb, "id": tvdb_id}}
    mapping = await subscription_episode_mapping(
        rss_ref if rss_ref is not None else rss_episode_ref({}, ""),
        mapping_sub, bangumi_id, metadata_ctx, sort=sort, overrides=old.get("mapping"),
    )
    tvdb_episode_number = mapping["tvdb"]["episode_number"]
    rel_path = format_download_path(
        config.RSS_PATH_TEMPLATE, sub, sort=sort, tvdb_episode=tvdb_episode_number,
        tmdb_episode=mapping["tmdb"]["episode_number"],
    ).lstrip("/")
    season_dir = str(Path(rss_base) / Path(rel_path).parent)
    show_dir = str(Path(season_dir).parent)
    qb = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
    if replace_existing and old.get("info_hash"):
        if old["info_hash"].lower() == torrent_hash.lower():
            raise HTTPException(400, "新种子与当前种子相同")
        await delete_torrent(qb, old["info_hash"], delete_files=True)
    info_hash = await add_torrent(qb, torrent_path, rss_base, guid)
    processing_result = {}
    try:
        files = await get_torrent_files(qb, info_hash)
        videos = [f for f in files if Path(f["name"]).suffix.lower() in VIDEO_EXTENSIONS]
        if len(videos) != 1:
            raise RuntimeError(f"种子中视频文件数量不为1 (found {len(videos)})")
        show_name = sub.get("name", str(bangumi_id))
        success = await generate_metadata(
            qb,
            info_hash,
            videos[0]["name"],
            season_dir=season_dir,
            show_dir=show_dir,
            series_name=sub.get("series_name") or show_name,
            metadata_ctx=metadata_ctx,
            base_path=rss_base,
            overwrite=replace_existing,
            episode_mapping=mapping,
            processing_result=processing_result,
        )
        if not success:
            raise RuntimeError("NFO 生成或文件重命名失败")
    except Exception:
        try:
            await delete_torrent(qb, info_hash, delete_files=False)
        except Exception:
            logger.exception("清理失败种子失败: hash=%s", info_hash[:12])
        raise
    try:
        await resume_torrent(qb, info_hash)
    except Exception:
        logger.warning("恢复种子失败: hash=%s", info_hash[:12], exc_info=True)
    mark_downloaded(
        bangumi_id, sort, guid, source, pub_date=pub_date,
        info_hash=torrent_hash,
        episode_mapping=mapping, download_path=processing_result["target_path"],
    )
    reset_fail_count(bangumi_id, sort)
    return torrent_hash
