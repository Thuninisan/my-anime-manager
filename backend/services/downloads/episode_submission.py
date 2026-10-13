"""按已确定的目标路径执行单视频种子下载。"""

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ... import config
from ...clients.qbittorrent import (
    login as qb_login, add_torrent, resume_torrent, delete_torrent,
    get_torrent_files, rename_file,
)

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".mka", ".avi", ".mov", ".ts", ".wmv", ".flv", ".webm"}


@dataclass(frozen=True)
class DownloadTarget:
    save_path: str
    relative_path: str

    @property
    def media_path(self) -> str:
        return str(Path(self.save_path) / self.relative_path)


@dataclass(frozen=True)
class DownloadResult:
    info_hash: str
    target_path: str
    created_new: bool


async def submit_torrent(torrent_path: str, target: DownloadTarget) -> DownloadResult:
    """暂停添加、重命名并恢复下载，不处理订阅、NFO 或历史记录。"""
    relative = PurePosixPath(target.relative_path)
    if (relative.is_absolute() or ".." in relative.parts or "\\" in target.relative_path
            or not relative.name or relative.suffix.lower() not in VIDEO_EXTENSIONS):
        raise ValueError("下载目标路径无效")
    root = Path(target.save_path).resolve()
    if not Path(target.media_path).resolve().is_relative_to(root):
        raise ValueError("下载目标路径超出保存目录")
    qb = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
    submission = {}
    info_hash = await add_torrent(qb, torrent_path, target.save_path, submission_result=submission)
    if not submission["created_new"]:
        raise RuntimeError("种子已存在于 qBittorrent，未修改已有任务")
    try:
        files = await get_torrent_files(qb, info_hash)
        videos = [f for f in files if Path(f["name"]).suffix.lower() in VIDEO_EXTENSIONS]
        if len(videos) != 1:
            raise RuntimeError(f"种子中视频文件数量不为1 (found {len(videos)})")
        if Path(videos[0]["name"]).suffix.lower() != relative.suffix.lower():
            raise ValueError("目标扩展名与视频文件不一致")
        if await rename_file(qb, info_hash, videos[0]["name"], relative.as_posix()) is False:
            raise RuntimeError("文件重命名失败")
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
    return DownloadResult(info_hash, target.media_path, created_new=True)
