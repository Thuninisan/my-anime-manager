"""按已记录的文件归属清理旧订阅，不递归删除作品目录。"""

import asyncio
from pathlib import Path

from sqlalchemy import select

from ... import config
from ...clients import qbittorrent as qb
from ...db.connection import new_session
from ...db.models import DownloadEpisode, TorrentCard, WorkflowRecovery
from ...db.download_episode_mapping import as_dict

MEDIA = {".mkv", ".mp4", ".avi", ".m2ts", ".ts", ".mov", ".wmv"}
SIDECARS = {".nfo", ".ass", ".ssa", ".srt", ".sub", ".idx", ".vtt", ".sup"}
IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def safe_path(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or len(path.parts) < 3:
        raise ValueError(f"无法安全确认文件路径：{path}")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(f"文件路径包含符号链接，已保留：{path}")
    return path


def ownership(bangumi_id):
    with new_session() as session:
        rows = list(session.scalars(select(DownloadEpisode).where(DownloadEpisode.bangumi_id != bangumi_id)))
        paths = {str(Path(row.download_path)) for row in rows if row.download_path}
        for value in tuple(paths):
            if Path(value).suffix.lower() in MEDIA:
                paths.update(str(path) for path in sidecars(Path(value)))
        hashes = {row.info_hash.lower() for row in rows if row.info_hash}
        # Torrent 合集的任务和媒体路径也不能被 RSS 清理误删。
        cards = list(session.scalars(select(TorrentCard)))
        hashes.update(card.info_hash.lower() for card in cards)
        def collect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if isinstance(child, str) and "path" in key and Path(child).is_absolute():
                        paths.add(str(Path(child)))
                    else:
                        collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        for card in cards:
            collect(card.extra_data)
            if card.completion_snapshot_json:
                import json
                collect(json.loads(card.completion_snapshot_json))
        for job in session.scalars(select(WorkflowRecovery).where(WorkflowRecovery.workflow_type == "torrent_processing")):
            if (job.checkpoint.get("processing") or {}).get("replace_bangumi_id") == bangumi_id:
                raise ValueError("当前订阅正在进行 BD 替换，请等待完成后再修改映射")
            collect(job.checkpoint)
    return paths, hashes


def shared(path, protected):
    path = Path(path)
    for value in protected:
        other = Path(value)
        if path == other or path.is_relative_to(other):
            return True
        if path.name in {"tvshow.nfo", "season.nfo"} and other.is_relative_to(path.parent):
            return True
    return False


def nfo_conflict(bangumi_id, path, mapping):
    """共享的剧集或季元数据只有在平台坐标兼容时才能重建。"""
    protected, _ = ownership(bangumi_id)
    if not shared(path, protected):
        return False
    with new_session() as session:
        rows = session.scalars(select(DownloadEpisode).where(DownloadEpisode.bangumi_id != bangumi_id))
        owners = [row for row in rows if row.download_path and Path(row.download_path).is_relative_to(path.parent)]
        if not owners:
            return True
        for row in owners:
            old = as_dict(row) or {}
            for provider in ("tmdb", "tvdb"):
                if old.get(f"{provider}_series_id") != mapping[provider]["series_id"]:
                    return True
                if path.name == "season.nfo" and old.get(f"{provider}_season_number") != mapping[provider]["season_number"]:
                    return True
    return False


def sidecars(media):
    """仅接纳与媒体同 stem 的已知字幕、NFO 和缩略图。"""
    found = {media, media.with_suffix(".nfo")}
    if media.parent.is_dir():
        for path in media.parent.iterdir():
            if path.name.startswith(media.stem + ".") and path.suffix.lower() in SIDECARS:
                found.add(path)
            if path.name.startswith(media.stem + "-thumb.") and path.suffix.lower() in IMAGES:
                found.add(path)
    return found


def identity(path):
    safe_path(path)
    try:
        stat = Path(path).stat()
    except FileNotFoundError:
        return None
    if not Path(path).is_file():
        raise ValueError(f"目标不是普通文件：{path}")
    return [stat.st_dev, stat.st_ino]


async def plan(bangumi_id, snapshot):
    protected, protected_hashes = ownership(bangumi_id)
    files, warnings, torrents = set(), [], []
    hashes = sorted({row["info_hash"].lower() for row in snapshot["episodes"].values() if row.get("info_hash")})
    if any(value in protected_hashes for value in hashes):
        raise ValueError("旧订阅的种子也被其他下载记录或 Torrent 合集使用，不能自动清理")
    for row in snapshot["episodes"].values():
        if row.get("download_path"):
            media = safe_path(row["download_path"])
            files.update(sidecars(media))
            season, show = media.parent, media.parent.parent
            for folder, names in ((season, {"season.nfo"}), (show, {"tvshow.nfo"})):
                if any(Path(value).is_relative_to(folder) for value in protected):
                    warnings.append(f"共享目录元数据保留：{folder}")
                    continue
                files.update(folder / name for name in names)
                if folder.is_dir():
                    for path in folder.iterdir():
                        image_names = {"folder", "poster", "fanart", "backdrop", "landscape", "logo", "clearlogo", "banner"}
                        if path.suffix.lower() in IMAGES and (path.stem in image_names or (
                                folder == season and path.stem.startswith("season") and path.stem.endswith("-poster"))):
                            files.add(path)
        elif not row.get("info_hash"):
            warnings.append("下载记录缺少实际媒体路径与种子信息，无法定位的文件将保留")
    if hashes:
        client = await qb.login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
        info = await qb.get_torrents_by_hashes(client, hashes, raise_on_error=True)
        for info_hash in hashes:
            if info_hash not in info:
                continue
            root = safe_path(str(Path(info[info_hash]["save_path"]) / "placeholder")).parent
            listed = await qb.get_torrent_files(client, info_hash)
            torrent_paths = []
            for item in listed:
                relative = Path(item["name"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("qBittorrent 文件清单包含越界路径，已停止清理")
                path = safe_path(str(root / relative))
                if shared(path, protected):
                    raise ValueError(f"种子包含共享文件，无法删除：{path}")
                torrent_paths.append(str(path))
                files.add(path)
            torrents.append({"hash": info_hash, "files": torrent_paths})
    output = []
    for path in sorted(files):
        if shared(path, protected):
            warnings.append(f"共享文件保留：{path}")
            continue
        output.append({"path": str(path), "identity": identity(path)})
    return {"files": output, "torrents": torrents, "warnings": sorted(set(warnings)),
            "media_count": sum(Path(item["path"]).suffix.lower() in MEDIA for item in output),
            "nfo_count": sum(Path(item["path"]).suffix.lower() == ".nfo" for item in output)}


async def execute(task, persist):
    plan = task["plan"]
    protected, protected_hashes = ownership(task["bangumi_id"])
    for torrent in plan["torrents"]:
        if torrent["hash"] in protected_hashes:
            raise ValueError("种子在预览后被其他记录使用，停止删除")
    # 删除前一次性检查整个清单，拒绝新出现或被替换的文件。
    for item in plan["files"]:
        current = identity(item["path"])
        if current is not None and current != item["identity"]:
            raise ValueError(f"文件在预览后被替换或新建，请重新预览：{item['path']}")
        if shared(item["path"], protected):
            raise ValueError(f"文件在预览后被其他记录使用：{item['path']}")
    task["deletion_started"] = True
    persist(task)
    if plan["torrents"]:
        client = await qb.login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
        for torrent in plan["torrents"]:
            info_hash = torrent["hash"]
            if info_hash in task.get("deleted_hashes", []):
                continue
            current_paths, current_hashes = ownership(task["bangumi_id"])
            if info_hash in current_hashes or any(shared(path, current_paths) for path in torrent["files"]):
                raise ValueError("旧种子或文件已被其他记录使用，停止清理")
            current = await qb.get_torrents_by_hashes(client, [info_hash], raise_on_error=True)
            if current:
                listed = await qb.get_torrent_files(client, info_hash)
                root = Path(current[info_hash]["save_path"])
                actual = sorted(str(safe_path(str(root / item["name"]))) for item in listed)
                if actual != sorted(torrent["files"]):
                    raise ValueError("种子文件清单在预览后发生变化，停止删除")
                await qb.delete_torrent(client, info_hash, delete_files=True)
                for _ in range(20):
                    if not await qb.get_torrents_by_hashes(client, [info_hash], raise_on_error=True):
                        break
                    await asyncio.sleep(0.5)
                else:
                    raise ValueError(f"qBittorrent 任务未删除：{info_hash}")
            task.setdefault("deleted_hashes", []).append(info_hash)
            task["results"].append({"path": info_hash, "status": "success", "message": "种子任务及源文件已删除"})
            persist(task)
    for item in plan["files"]:
        path = safe_path(item["path"])
        try:
            current_paths, _ = ownership(task["bangumi_id"])
            if shared(path, current_paths):
                raise ValueError("文件已被其他记录使用，停止清理")
            current = identity(path)
            if current is not None and current != item["identity"]:
                raise ValueError("文件已被替换，停止删除")
            path.unlink(missing_ok=True)
            task["results"].append({"path": str(path), "status": "success", "message": "文件已清理"})
        except Exception as exc:
            task["results"].append({"path": str(path), "status": "failed", "message": str(exc)})
            persist(task)
            raise
        persist(task)
