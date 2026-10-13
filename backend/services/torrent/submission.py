"""Submit a confirmed Torrent preview and dispatch its persisted processing plan."""

# 职责边界：本文件负责手动 Torrent 下载提交，从可信预览恢复请求、校验选择，
# 协调 qBittorrent 添加/文件优先级、下载前元数据生成、业务记录与检查点保存，
# 最后调用 recovery.start_monitor() 启动后处理；调用方是下载 API。
# 本文件不管理后台任务注册表、恢复调度、租约或后处理阶段，也不实现文件整理、
# 字体处理和预览搜索。API 仅转发请求；这里不导入 backend.api。
# 本轮保留既有 HTTPException、响应结构和外部副作用顺序；异常分层另行迁移。

import logging
import re
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from ... import config
from ...clients import bangumi as bgm_client
from ...clients.qbittorrent import (
    add_torrent,
    get_torrent_files,
    login as qb_login,
    resume_torrent,
    set_file_priority,
)
from ...db import torrents as torrent_store
from ...utils.torrent_file_reader import read_torrent_file_list
from ...utils.torrent_hash import compute_info_hash
from .. import bd_replacement, rss_poster
from ..nfo import format_download_path
from . import recovery
from .metadata import pre_generate_nfo
from .monitor import build_processing
from .preview import derive_series_name

logger = logging.getLogger(__name__)


async def _torrent_card_meta(bangumi_id: int) -> dict:
    """Resolve card metadata once at submission; reads never contact Bangumi."""
    if bangumi_id <= 0:
        return {}
    try:
        subject = await bgm_client.get_subject(bangumi_id)
        poster_path = await rss_poster.get_poster(bangumi_id)
        return {
            "show_name": subject.get("name_cn") or subject.get("name") or "",
            "bgm_rating": (subject.get("rating") or {}).get("score") or 0,
            "poster_url": rss_poster.poster_url(bangumi_id) if poster_path else "",
        }
    except Exception:
        logger.exception("Torrent 卡片元数据获取失败: Bangumi %s", bangumi_id)
        return {}


def _torrent_tags(torrent_name: str, files: list[dict]) -> tuple[str, str]:
    text = " ".join([torrent_name, *(f.get("torrent_path", "") for f in files if not f.get("is_subtitle"))])
    group = "ktnbytes" if re.search(r"ktnbytes|343-labs", torrent_name, re.I) else ""
    codec = "av1" if re.search(r"(?<![a-z0-9])(?:av1|av01)(?![a-z0-9])", text, re.I) else (
        "h265" if re.search(r"(?<![a-z0-9])(?:h[. _-]?265|x265|hevc)(?![a-z0-9])", text, re.I) else ""
    )
    return group, codec


def _torrent_show_name(snapshot: dict, files: list[dict]) -> str:
    videos = [item for item in files if not item.get("is_subtitle")]
    return next((item["bangumi_show_name"] for item in videos if item.get("bangumi_show_name")), "")


def _prepare_request(body: dict) -> tuple[dict, dict | None]:
    """Restore trusted preview data and validate the selected download."""
    if "preview_id" not in body:
        raise HTTPException(409, "preview_schema_outdated: create a canonical preview")
    from .preview_session import restore_download_request
    body = restore_download_request(body)
    torrent_path = body.get("torrent_path", "")
    resource_id = body.get("resource_id")
    if resource_id is not None:
        from ...db import resources as resource_store
        try:
            source_record = resource_store.get_resource(int(resource_id))
        except (TypeError, ValueError):
            raise HTTPException(400, "无效的资源 ID")
        if source_record is None:
            raise HTTPException(404, "资源不存在")
        expected = resource_store.torrent_file_path(source_record["source"], source_record["source_id"])
        if Path(source_record["torrent_path"]) != expected:
            raise HTTPException(400, "资源种子路径无效")
        torrent_path = str(expected)
    files: list[dict] = body.get("files", [])
    replace_bangumi_id = body.get("replace_bangumi_id")

    if not torrent_path or not Path(torrent_path).is_file():
        raise HTTPException(400, "种子文件不存在")
    if not files:
        raise HTTPException(400, "文件列表为空")
    episodes = None
    if replace_bangumi_id is not None:
        try:
            replace_bangumi_id = int(replace_bangumi_id)
        except (TypeError, ValueError):
            raise HTTPException(400, "无效的订阅 ID")
        episodes = bd_replacement.validate_mapping(replace_bangumi_id, files)

    body["torrent_path"] = torrent_path
    body["replace_bangumi_id"] = replace_bangumi_id
    return body, episodes


async def _build_record(
    info_hash: str, torrent_name: str, files: list[dict],
    preview_snapshot: dict, processing: dict,
) -> dict:
    """Build the business card and processing checkpoint saved in one transaction."""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    encoding_group, video_codec = _torrent_tags(torrent_name, files)
    bangumi_ids = sorted({int(f["episode_mapping"]["bangumi"]["subject_id"]) for f in files if f["episode_mapping"]["bangumi"]["subject_id"]})
    card_meta = await _torrent_card_meta(bangumi_ids[0]) if bangumi_ids else {}
    record = {
        "info_hash": info_hash,
        "torrent_name": torrent_name,
        "show_name": card_meta.get("show_name") or _torrent_show_name(preview_snapshot, files),
        "bgm_rating": card_meta.get("bgm_rating", 0),
        "poster_url": card_meta.get("poster_url", ""),
        "status": "downloading",
        "created_at": now,
        "updated_at": now,
        "encoding_group": encoding_group,
        "video_codec": video_codec,
        "bangumi_ids": bangumi_ids,
    }
    record["processing"] = processing
    return record


async def submit_download(body: dict) -> dict:
    """Add a torrent to qBittorrent with selective file download.

    Only the files listed in *files* (and their matching subtitles) are
    downloaded.  After the download completes a background task creates
    hardlinks for video files and copies subtitle files into the configured
    ``TORRENT_HARDLINK_PATH`` directory.

    The immutable canonical preview snapshot supplies NFO
    files and images generated **before** the torrent is resumed,
    matching the batch/scan flow behaviour.
    """
    body, episodes = _prepare_request(body)
    torrent_path = body["torrent_path"]
    resource_id = body.get("resource_id")
    torrent_name = body.get("torrent_name", "")
    files: list[dict] = body.get("files", [])
    uploaded_subtitles: list[dict] = body.get("uploaded_subtitles", [])
    preview_snapshot = body["preview_snapshot"]
    replace_bangumi_id = body.get("replace_bangumi_id")

    download_path = config.TORRENT_DOWNLOAD_PATH  # qBittorrent 下载暂存目录
    hardlink_root = (config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH) if replace_bangumi_id is not None else config.TORRENT_HARDLINK_PATH

    # ── Read the full file list from the torrent ──
    try:
        full_file_list = read_torrent_file_list(torrent_path)
    except Exception as e:
        raise HTTPException(400, f"无法读取种子文件: {e}")

    # Build a set of torrent paths that should be downloaded
    download_set: set[str] = {f["torrent_path"] for f in files}

    # ── Login to qBittorrent ──
    try:
        client = await qb_login(
            config.QBITTORRENT_URL,
            config.QBITTORRENT_USERNAME,
            config.QBITTORRENT_PASSWORD,
        )
    except Exception as e:
        raise HTTPException(500, f"qBittorrent 连接失败: {e}")

    if replace_bangumi_id is not None:
        await bd_replacement.old_files(
            client, replace_bangumi_id, episodes, compute_info_hash(torrent_path),
        )
        for item in files:
            if item.get("is_subtitle"):
                continue
            sort = int(item["episode_mapping"]["bangumi"]["episode_absolute"])
            sub, _, _ = bd_replacement.downloader.resolve_episode_paths(replace_bangumi_id, sort)
            tvdb_episode_number = sort + (sub.get("tvdb", {}).get("ep_offset") or 0)
            rel = format_download_path(config.RSS_PATH_TEMPLATE, sub, sort=sort, tvdb_episode=tvdb_episode_number).lstrip("/")
            item["replacement_target"] = str(Path(rel).with_suffix(Path(item["torrent_path"]).suffix))

    # ── Add torrent (paused) ──
    try:
        info_hash = await add_torrent(client, torrent_path, download_path, torrent_name)
        logger.info("种子已添加 [%s]: hash=%s", torrent_name, info_hash[:12])
    except Exception as e:
        raise HTTPException(500, f"添加种子失败: {e}")

    # ── Set file priorities: 1 for files we want, 0 for the rest ──
    try:
        # Get file list from qBittorrent to map paths → indices
        qb_files = await get_torrent_files(client, info_hash)
        skip_indices: list[int] = []
        download_indices: list[int] = []
        for idx, f in enumerate(qb_files):
            fname = f.get("name", "")
            if fname in download_set:
                download_indices.append(idx)
            else:
                skip_indices.append(idx)

        if skip_indices:
            await set_file_priority(client, info_hash, skip_indices, 0)
            logger.info("跳过 %d 个文件", len(skip_indices))

        if download_indices:
            await set_file_priority(client, info_hash, download_indices, 1)
            logger.info("下载 %d 个文件", len(download_indices))
    except Exception as e:
        logger.warning("设置文件优先级失败 (将继续下载所有文件): %s", e)

    # ── Derive series name for path template ──
    series_name = derive_series_name(preview_snapshot, files)

    # ── Generate NFO + images BEFORE resuming (if metadata provided) ──
    # pre_generate_nfo also detects movies — is_movie drives the monitor's
    # hardlink root for the flat movie layout.
    is_movie, nfo_generated, movie_meta = await pre_generate_nfo(
        preview_snapshot, files, torrent_name, hardlink_root, series_name,
    )

    context = {
        "torrent_name": torrent_name,
        "files": files,
        "uploaded_subtitles": uploaded_subtitles,
        "hardlink_root": config.MOVIE_HARDLINK_PATH if is_movie else hardlink_root,
        "series_name": series_name,
        "skip_nfo": nfo_generated,
        "movie_meta": movie_meta,
        "replace_bangumi_id": replace_bangumi_id,
        "replaced_history": episodes if replace_bangumi_id is not None else None,
    }
    try:
        processing = build_processing(context)
    except Exception as e:
        logger.exception("创建 Torrent 后处理计划失败: %s", torrent_name)
        raise HTTPException(500, f"无法创建后处理计划: {e}")

    # ── Resume download ──
    try:
        await resume_torrent(client, info_hash)
        logger.info("下载已恢复 [%s]", torrent_name)
    except Exception as e:
        raise HTTPException(500, f"恢复下载失败: {e}")

    # Persist the card and minimal file operations together before dispatching.
    record = await _build_record(info_hash, torrent_name, files, preview_snapshot, processing)
    try:
        torrent_store.save_torrent(record)
    except Exception as e:
        logger.exception("保存 Torrent 记录失败: %s", info_hash)
        raise HTTPException(500, f"种子已开始下载，但保存记录失败: {e}")
    recovery.start_monitor(record, context)

    # Clean up the temp torrent file (already added to qBittorrent)
    if resource_id is None:
        Path(torrent_path).unlink(missing_ok=True)

    return {
        "ok": True,
        "info_hash": info_hash,
        "message": f"种子已添加，选择性下载 {len(download_indices)}/{len(qb_files)} 个文件。下载完成后自动创建硬链接。",
    }
