"""Finalize a manually supplied BD torrent after every selected video is linked."""

import logging
import os
from pathlib import Path

from fastapi import HTTPException

from .. import config, data
from ..clients.qbittorrent import delete_torrent, get_torrent_files, get_torrents_by_hashes
from . import downloader

logger = logging.getLogger(__name__)


def validate_mapping(bangumi_id: int, files: list[dict]) -> dict[str, dict]:
    """Require one BD video for every recorded episode of this subscription."""
    downloader.resolve_episode_paths(bangumi_id, 1)
    episodes = data.get_all_episodes(bangumi_id)
    if not episodes:
        raise HTTPException(400, "此订阅没有可替换的下载记录")
    expected = {int(sort) for sort in episodes}
    mapped = [int(f["episode_mapping"]["bangumi"]["episode_absolute"] or 0) for f in files if not f.get("is_subtitle")]
    if any(int(f["episode_mapping"]["bangumi"]["subject_id"] or 0) != bangumi_id for f in files):
        raise HTTPException(400, "BD 文件包含其他 Bangumi 条目的映射")
    if len(mapped) != len(set(mapped)) or set(mapped) != expected:
        raise HTTPException(400, "BD 视频必须逐集覆盖全部 RSS 下载记录，且不能重复映射")
    if any(not ep.get("info_hash") for ep in episodes.values()):
        raise HTTPException(400, "部分 RSS 下载记录没有种子哈希，无法安全清理")
    return episodes


async def old_files(qb, bangumi_id: int, episodes: dict[str, dict], new_hash: str) -> tuple[list[str], set[Path]]:
    """Find old qBittorrent data and its media hardlinks before BD linking."""
    hashes = sorted({str(ep["info_hash"]) for ep in episodes.values()})
    if new_hash in hashes:
        raise HTTPException(400, "BD 种子与现有 RSS 种子相同")
    torrents = await get_torrents_by_hashes(qb, hashes)
    if set(torrents) != set(hashes):
        raise HTTPException(400, "部分 RSS 种子已不在 qBittorrent 中")
    old_inodes: set[tuple[int, int]] = set()
    for h in hashes:
        base = Path(torrents[h]["save_path"])
        for item in await get_torrent_files(qb, h):
            path = base / item["name"]
            if path.is_file():
                stat = path.stat()
                old_inodes.add((stat.st_dev, stat.st_ino))
    if not old_inodes:
        raise HTTPException(400, "未找到 RSS 本地文件")
    links: set[Path] = set()
    for sort in episodes:
        _, season_dir, _ = downloader.resolve_episode_paths(bangumi_id, int(sort))
        root = Path(season_dir)
        if root.is_dir():
            for path in root.iterdir():
                if path.is_file():
                    stat = path.stat()
                    if (stat.st_dev, stat.st_ino) in old_inodes:
                        links.add(path)
    return hashes, links


async def finalize(qb, bangumi_id: int, expected: dict[str, dict], hashes: list[str], old_links: set[Path], linked: set[Path] | None, required: int) -> bool:
    """Keep RSS state intact unless all BD videos were linked successfully."""
    if linked is None or len(linked) != required:
        logger.error("BD 替换未完成: bangumi=%s linked=%s required=%s", bangumi_id, len(linked or ()), required)
        return False
    if any(not path.is_file() for path in linked):
        logger.error("BD 临时硬链接缺失: bangumi=%s", bangumi_id)
        return False
    if data.get_all_episodes(bangumi_id) != expected:
        logger.error("BD 替换期间 RSS 历史已变化: bangumi=%s", bangumi_id)
        return False
    for h in hashes:
        try:
            await delete_torrent(qb, h, delete_files=True)
        except Exception:
            logger.exception("删除旧 RSS 种子失败: %s", h)
            return False
    final_paths = {path.with_name(path.name.removesuffix(".mam-bd-pending")) for path in linked}
    for path in linked:
        if not path.name.endswith(".mam-bd-pending"):
            logger.error("BD 临时路径无效: %s", path)
            return False
        try:
            os.replace(path, path.with_name(path.name.removesuffix(".mam-bd-pending")))
        except OSError:
            logger.exception("安装 BD 硬链接失败: %s", path)
            return False
    for path in old_links - final_paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("删除旧 RSS 硬链接失败: %s", path)
            return False
    data.clear_download_history(bangumi_id)
    data.remove_subscription(bangumi_id)
    return True
