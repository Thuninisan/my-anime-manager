"""API routes: /api/torrent/*."""

import asyncio
import logging
import os
import re
import tempfile
import traceback
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import config
from . import state
from ..clients import bangumi as bgm_client
from ..clients.qbittorrent import (
    add_torrent,
    get_torrent_files,
    login as qb_login,
    resume_torrent,
    set_file_priority,
)
from ..services.torrent_monitor import monitor_download
from ..services.torrent_metadata import pre_generate_nfo
from ..services.torrent_preview import derive_series_name
from ..utils.paths import SUBTITLE_DIR
from ..utils.torrent_file_reader import read_torrent_file_list

router = APIRouter()
logger = logging.getLogger(__name__)

# ── /api/torrent/subtitle/upload ──

# Allowed subtitle file extensions
_ALLOWED_SUB_EXTENSIONS: set[str] = {".ass", ".ssa", ".srt", ".sub", ".idx", ".vtt", ".ttml", ".sbv", ".dfxp"}



@router.post("/api/torrent/subtitle/upload")
async def subtitle_upload(
    file: UploadFile = File(...),
    torrent_name: str = Form(...),
    target_stem: str = Form(""),
):
    """Upload a subtitle file for a specific torrent.

    The file is stored under ``data/subtitles/{torrent_name}/`` so it can be
    copied alongside the media files during the confirm phase.

    If *target_stem* is provided the file is renamed to ``{target_stem}{ext}``
    so the frontend can match it to a specific video file by filename stem
    (used by batch folder upload).

    Only common subtitle formats are accepted (.ass, .srt, etc.).
    """
    if not file.filename:
        raise HTTPException(400, "未提供文件名")

    ext = Path(file.filename).suffix.lower()
    if ext not in _ALLOWED_SUB_EXTENSIONS:
        raise HTTPException(
            400,
            f"不支持的字幕格式: {ext}。支持的格式: {', '.join(sorted(_ALLOWED_SUB_EXTENSIONS))}",
        )

    # Sanitise torrent_name for use as directory name
    safe_torrent_name = re.sub(r'[<>:"/\\|?*]', "_", torrent_name).strip()
    if not safe_torrent_name:
        raise HTTPException(400, "种子名称为空")

    dest_dir = SUBTITLE_DIR / safe_torrent_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Determine the stored filename: use target_stem if provided, else original name
    if target_stem:
        safe_stem = re.sub(r'[<>:"/\\|?*]', "_", target_stem).strip()
        if not safe_stem:
            raise HTTPException(400, "target_stem 无效")
        dest_filename = f"{safe_stem}{ext}"
    else:
        dest_filename = file.filename

    # Avoid overwriting — append a counter if the file already exists
    dest_path = dest_dir / dest_filename
    if dest_path.exists():
        stem, suffix = dest_path.stem, dest_path.suffix
        counter = 1
        while dest_path.exists():
            dest_path = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    content = await file.read()
    dest_path.write_bytes(content)

    logger.info("字幕上传成功: %s → %s", file.filename, dest_path)

    return {
        "ok": True,
        "filename": dest_path.name,
        "original_filename": file.filename,
        "torrent_name": safe_torrent_name,
        "stored_path": str(dest_path),
    }


@router.delete("/api/torrent/subtitle/delete")
async def subtitle_delete(torrent_name: str, filename: str):
    """Delete a user-uploaded subtitle file.

    Only removes files under ``data/subtitles/{torrent_name}/`` — the endpoint
    rejects paths that attempt directory traversal.
    """
    # Sanitise inputs to prevent directory traversal
    safe_torrent_name = re.sub(r'[<>:"/\\|?*]', "_", torrent_name).strip()
    safe_filename = Path(filename).name  # strip any directory components

    if not safe_torrent_name or not safe_filename:
        raise HTTPException(400, "种子名称或文件名为空")

    file_path = SUBTITLE_DIR / safe_torrent_name / safe_filename

    # Resolve and verify the path stays within the subtitles directory
    try:
        file_path = file_path.resolve()
        SUBTITLE_DIR.resolve()
        if not str(file_path).startswith(str(SUBTITLE_DIR.resolve()) + os.sep):
            raise HTTPException(403, "路径越界")
    except (ValueError, OSError):
        raise HTTPException(400, "无效的文件路径")

    if not file_path.is_file():
        raise HTTPException(404, f"字幕文件不存在: {safe_filename}")

    file_path.unlink()
    logger.info("字幕已删除: %s", file_path)

    # Clean up empty parent directory
    parent = file_path.parent
    if parent != SUBTITLE_DIR and not any(parent.iterdir()):
        parent.rmdir()

    return {"ok": True, "deleted": safe_filename}


# ── /api/torrent/parse-and-search ──

@router.post("/api/torrent/parse-and-search")
async def torrent_parse_and_search(file: UploadFile = File(...)):
    """Parse a .torrent file and search TMDB + Bangumi for matched shows.

    Independent endpoint — does NOT use the existing build_preview flow.
    Upload a .torrent, get back parsed file list + deduplicated show names
    + parallel TMDB/Bangumi search results.

    Returns:
        JSON with torrent_name, parsed_files, skipped_files, show_names,
        and search_results (tmdb / bangumi each with default + backup).
    """
    if not file.filename or not file.filename.endswith(".torrent"):
        raise HTTPException(400, "请上传 .torrent 文件")

    # Save uploaded file to temp location
    with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        from ..services.torrent_preview import parse_and_search
        result = await parse_and_search(tmp_path)
    except Exception as e:
        Path(tmp_path).unlink(missing_ok=True)
        traceback.print_exc()
        raise HTTPException(400, str(e))

    # Keep the temp file — the download endpoint needs it later
    return result


# ── /api/torrent/bangumi/{id}/episodes ──

@router.get("/api/torrent/bangumi/{bangumi_id}/episodes")
async def torrent_bangumi_episodes(bangumi_id: int):
    """Fetch episode data for a Bangumi subject (main + SP).

    Used by the frontend to add extra Bangumi entries to the match table.
    """
    try:
        eps_main = await bgm_client.get_episodes(bangumi_id, ep_type=0)
    except Exception:
        eps_main = []
    try:
        eps_sp = await bgm_client.get_episodes(bangumi_id, ep_type=1)
    except Exception:
        eps_sp = []

    try:
        subject = await bgm_client.get_subject(bangumi_id)
        name = subject.get("name_cn") or subject.get("name", str(bangumi_id))
    except Exception:
        name = str(bangumi_id)

    all_eps = (eps_main or []) + (eps_sp or [])
    clean_eps = []
    for ep in all_eps:
        entry = {
            "sort": ep.get("sort") or ep.get("ep", 0),
            "id": ep["id"],
            "name": ep.get("name", ""),
        }
        cn = ep.get("name_cn")
        if cn and cn != entry["name"]:
            entry["name_cn"] = cn
        clean_eps.append(entry)
    clean_eps.sort(key=lambda x: x["sort"])

    return {
        "id": bangumi_id,
        "name": name,
        "episodes": clean_eps,
    }


# ── /api/torrent/download ──


@router.post("/api/torrent/download")
async def torrent_download(body: dict):
    """Add a torrent to qBittorrent with selective file download.

    Only the files listed in *files* (and their matching subtitles) are
    downloaded.  After the download completes a background task creates
    hardlinks for video files and copies subtitle files into the configured
    ``TORRENT_HARDLINK_PATH`` directory.

    If *preview_data* is present (the full parse-and-search result), NFO
    files and images are generated **before** the torrent is resumed,
    matching the batch/scan flow behaviour.
    """
    torrent_path = body.get("torrent_path", "")
    torrent_name = body.get("torrent_name", "")
    files: list[dict] = body.get("files", [])
    uploaded_subtitles: list[dict] = body.get("uploaded_subtitles", [])
    preview_data: dict | None = body.get("preview_data")

    if not torrent_path or not Path(torrent_path).is_file():
        raise HTTPException(400, "种子文件不存在")
    if not files:
        raise HTTPException(400, "文件列表为空")

    download_path = config.TORRENT_DOWNLOAD_PATH  # qBittorrent 下载暂存目录
    hardlink_root = config.TORRENT_HARDLINK_PATH   # 下载完成后硬链接目标目录

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
    series_name = derive_series_name(preview_data)

    # ── Generate NFO + images BEFORE resuming (if metadata provided) ──
    # pre_generate_nfo also detects movies — is_movie drives the monitor's
    # hardlink root for the flat movie layout.
    is_movie, nfo_generated, movie_meta = await pre_generate_nfo(
        preview_data, files, torrent_name, hardlink_root, series_name,
    )

    # ── Resume download ──
    try:
        await resume_torrent(client, info_hash)
        logger.info("下载已恢复 [%s]", torrent_name)
    except Exception as e:
        raise HTTPException(500, f"恢复下载失败: {e}")

    # ── Start background monitor ──
    # Wrap the monitor so the task tracker is always cleaned up, even if
    # the monitor dies with an unexpected exception.
    async def _tracked_monitor() -> None:
        try:
            await monitor_download(
                info_hash=info_hash,
                torrent_name=torrent_name,
                files=files,
                uploaded_subtitles=uploaded_subtitles,
                hardlink_root=config.MOVIE_HARDLINK_PATH if is_movie else hardlink_root,
                series_name=series_name,
                skip_nfo=nfo_generated,
                movie_meta=movie_meta,
            )
        finally:
            state._download_tasks.pop(info_hash, None)

    task = asyncio.create_task(_tracked_monitor())
    state._download_tasks[info_hash] = task

    # Clean up the temp torrent file (already added to qBittorrent)
    Path(torrent_path).unlink(missing_ok=True)

    return {
        "ok": True,
        "info_hash": info_hash,
        "message": f"种子已添加，选择性下载 {len(download_indices)}/{len(qb_files)} 个文件。下载完成后自动创建硬链接。",
    }


