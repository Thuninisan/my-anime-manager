"""API routes: /api/torrent/*."""

import asyncio
import logging
import os
import re
import shutil
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
    get_torrents_by_hashes,
    login as qb_login,
    resume_torrent,
    set_file_priority,
)
from ..utils.torrent_file_reader import read_torrent_file_list

router = APIRouter()
logger = logging.getLogger(__name__)

# ── /api/torrent/subtitle/upload ──

# Allowed subtitle file extensions
_ALLOWED_SUB_EXTENSIONS: set[str] = {".ass", ".ssa", ".srt", ".sub", ".idx", ".vtt", ".ttml", ".sbv", ".dfxp"}

# Subtitle storage root (under the data directory)
_SUBTITLE_DIR = Path(__file__).parent / "data" / "subtitles"


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

    dest_dir = _SUBTITLE_DIR / safe_torrent_name
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

    file_path = _SUBTITLE_DIR / safe_torrent_name / safe_filename

    # Resolve and verify the path stays within the subtitles directory
    try:
        file_path = file_path.resolve()
        _SUBTITLE_DIR.resolve()
        if not str(file_path).startswith(str(_SUBTITLE_DIR.resolve()) + os.sep):
            raise HTTPException(403, "路径越界")
    except (ValueError, OSError):
        raise HTTPException(400, "无效的文件路径")

    if not file_path.is_file():
        raise HTTPException(404, f"字幕文件不存在: {safe_filename}")

    file_path.unlink()
    logger.info("字幕已删除: %s", file_path)

    # Clean up empty parent directory
    parent = file_path.parent
    if parent != _SUBTITLE_DIR and not any(parent.iterdir()):
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


def _sanitize_path_component(name: str) -> str:
    """Remove characters that are illegal in directory / file names."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def _make_sub_for_path(f: dict, series_name: str = "") -> dict:
    """Build a pseudo-subscription dict for :func:`format_download_path`."""
    bgm_name = f.get("bangumi_show_name", "")
    return {
        "name": bgm_name,
        "series_name": series_name or bgm_name,
        "bgm": {
            "subject_name": bgm_name,
            "season": 1,
        },
        "tvdb": {
            "season": f.get("tvdb_season") or f.get("tmdb_season", 1),
        },
        "tmdb": {
            "season": f.get("tmdb_season", 1),
        },
    }


def _find_tmdb_id(preview_data: dict | None, show_name: str) -> int:
    """Find TMDB ID from preview_data for a given show name."""
    if not preview_data:
        return 0
    search_results = preview_data.get("search_results", {})
    for entry in search_results.values():
        tmdb = entry.get("tmdb", {}) if isinstance(entry, dict) else {}
        if tmdb.get("id"):
            return tmdb["id"]
    return 0


def _find_tvdb_id(preview_data: dict | None, bgm_id: int) -> int:
    """Find TVDB ID from preview_data's map_entries for a given BGM ID."""
    if not preview_data or not bgm_id:
        return 0
    search_results = preview_data.get("search_results", {})
    for entry in search_results.values():
        map_entries = entry.get("map_entries", []) if isinstance(entry, dict) else []
        for me in map_entries:
            if me.get("bangumi_id") == bgm_id and me.get("tvdb_id"):
                return me["tvdb_id"]
    return 0


def _derive_series_name(preview_data: dict | None) -> str:
    """Derive the root series name for path template ``{series_name}``.

    Priority: TMDB name from ``search_results`` → BGM name from
    ``episode_data.bangumi``.  This mirrors the RSS enrichment flow
    which prefers TMDB zh-CN over BGM.
    """
    if not preview_data:
        return ""

    # 1. Try TMDB name from search_results (usually Chinese or best
    #    available localised title)
    search_results = preview_data.get("search_results", {})
    for entry in search_results.values():
        if isinstance(entry, dict):
            tmdb = entry.get("tmdb")
            if isinstance(tmdb, dict) and tmdb.get("name"):
                return tmdb["name"]

    # 2. Fallback: first BGM entry from episode_data
    episode_data = preview_data.get("episode_data", {})
    bgm_data: dict = episode_data.get("bangumi", {})
    if bgm_data:
        first = next(iter(bgm_data.values()))
        if isinstance(first, dict):
            return first.get("name", "")

    return ""


async def _monitor_download(
    info_hash: str,
    torrent_name: str,
    files: list[dict],
    uploaded_subtitles: list[dict],
    hardlink_root: str,
    series_name: str = "",
    *,
    skip_nfo: bool = False,
    movie_meta: dict | None = None,
):
    """Background task: poll qBittorrent until download completes, then
    create hardlinks / copy subtitles.

    When *skip_nfo* is True the inline NFO generation is skipped (it was
    already done before the torrent was resumed).
    """
    subtitle_dir = _SUBTITLE_DIR / _sanitize_path_component(torrent_name)

    # Login for the background task
    try:
        client = await qb_login(
            config.QBITTORRENT_URL,
            config.QBITTORRENT_USERNAME,
            config.QBITTORRENT_PASSWORD,
        )
    except Exception as e:
        logger.error("下载监控登录失败 [%s]: %s", torrent_name, e)
        return

    import time
    deadline = time.monotonic() + 86400  # 24h max
    while time.monotonic() < deadline:
        await asyncio.sleep(5)
        try:
            torrents = await get_torrents_by_hashes(client, [info_hash])
        except Exception as e:
            logger.warning("下载监控轮询失败 [%s]: %s", torrent_name, e)
            continue

        t = torrents.get(info_hash)
        if not t:
            continue

        progress = t.get("progress", 0)
        state = t.get("state", "")

        if progress >= 1.0 or "paused" in state.lower() or "stopped" in state.lower() or "completed" in state.lower():
            logger.info("下载完成 [%s] (%.1f%%)", torrent_name, progress * 100)
            if progress < 1.0:
                logger.warning("种子状态异常 (progress=%.2f, state=%s), 仍然尝试创建文件", progress, state)

            save_path = t.get("save_path", hardlink_root)
            logger.info("下载完成 [%s], 开始创建硬链接/复制字幕...", torrent_name)

            created = 0

            if movie_meta:
                # ── Movie mode: flat structure {hardlink_root}/{tmdb_name}/{tmdb_name}.ext ──
                tmdb_name = movie_meta["tmdb_name"]
                movie_dir = Path(hardlink_root) / tmdb_name
                movie_dir.mkdir(parents=True, exist_ok=True)

                for f in files:
                    torrent_path = f["torrent_path"]
                    is_sub = f.get("is_subtitle", False)
                    src_ext = Path(torrent_path).suffix
                    src_path = Path(save_path) / torrent_path

                    if is_sub:
                        dest_path = movie_dir / f"{tmdb_name}{src_ext}"
                    else:
                        dest_path = movie_dir / f"{tmdb_name}{src_ext}"

                    try:
                        if src_path.exists():
                            if is_sub:
                                shutil.copy2(src_path, dest_path)
                            else:
                                if dest_path.exists():
                                    dest_path.unlink()
                                os.link(src_path, dest_path)
                            created += 1
                            logger.info("   %s → %s [%s]", src_path.name, dest_path, "copy" if is_sub else "hardlink")
                        else:
                            logger.warning("   源文件不存在: %s", src_path)
                    except OSError as e:
                        logger.error("   创建文件失败: %s → %s — %s", src_path, dest_path, e)

                # Copy user-uploaded subtitles
                for usub in uploaded_subtitles:
                    stored_name = usub.get("stored_filename", "")
                    src_sub = subtitle_dir / stored_name
                    if not src_sub.exists():
                        logger.warning("   上传的字幕文件不存在: %s", src_sub)
                        continue
                    dest_path = movie_dir / f"{tmdb_name}{src_sub.suffix}"
                    try:
                        shutil.copy2(src_sub, dest_path)
                        created += 1
                        logger.info("   [uploaded] %s → %s", stored_name, dest_path)
                    except OSError as e:
                        logger.error("   复制上传字幕失败: %s → %s — %s", src_sub, dest_path, e)

            else:
                # ── TV mode: path template ──
                template = config.RSS_PATH_TEMPLATE
                from ..services.nfo import format_download_path
                from ..services.nfo import (
                    write_episode_files,
                    generate_tv_show_nfo,
                    generate_season_nfo,
                )

                seen_show_dirs: set[str] = set()
                seen_season_dirs: set[str] = set()

                for f in files:
                    torrent_path = f["torrent_path"]
                    is_sub = f.get("is_subtitle", False)
                    src_ext = Path(torrent_path).suffix

                    sub = _make_sub_for_path(f, series_name)
                    tvdb_ep = f.get("tvdb_episode") or 0
                    tmdb_ep = f.get("tmdb_episode") or 0

                    rel_path = format_download_path(
                        template, sub,
                        tvdb_episode=tvdb_ep, tmdb_episode=tmdb_ep,
                    ).lstrip("/")
                    # Replace extension with the actual source extension
                    rel_path = str(Path(rel_path).with_suffix(src_ext))

                    dest_path = Path(hardlink_root) / rel_path
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    # Source: qBittorrent save_path / torrent_path
                    src_path = Path(save_path) / torrent_path

                    try:
                        if src_path.exists():
                            if is_sub:
                                shutil.copy2(src_path, dest_path)
                            else:
                                if dest_path.exists():
                                    dest_path.unlink()
                                os.link(src_path, dest_path)
                            created += 1
                            logger.info("   %s → %s [%s]", src_path.name, dest_path, "copy" if is_sub else "hardlink")
                        else:
                            logger.warning("   源文件不存在: %s", src_path)
                    except OSError as e:
                        logger.error("   创建文件失败: %s → %s — %s", src_path, dest_path, e)

                # Copy user-uploaded subtitles
                for usub in uploaded_subtitles:
                    stored_name = usub.get("stored_filename", "")
                    src_sub = subtitle_dir / stored_name
                    if not src_sub.exists():
                        logger.warning("   上传的字幕文件不存在: %s", src_sub)
                        continue

                    sub = _make_sub_for_path(usub, series_name)
                    tvdb_ep = usub.get("tvdb_episode") or 0
                    tmdb_ep = usub.get("tmdb_episode") or 0

                    rel_path = format_download_path(
                        template, sub,
                        tvdb_episode=tvdb_ep, tmdb_episode=tmdb_ep,
                    ).lstrip("/")
                    rel_path = str(Path(rel_path).with_suffix(src_sub.suffix))

                    dest_path = Path(hardlink_root) / rel_path
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    try:
                        shutil.copy2(src_sub, dest_path)
                        created += 1
                        logger.info("   [uploaded] %s → %s", stored_name, dest_path)
                    except OSError as e:
                        logger.error("   复制上传字幕失败: %s → %s — %s", src_sub, dest_path, e)

            # ── Generate NFO files (skipped if already done pre-resume) ──
            # Movie: always skip (pre-generated or skipped entirely)
            # TV: generate inline if skip_nfo is False
            nfo_generated = 0
            if movie_meta:
                logger.info("电影 NFO 已预生成，跳过内联 NFO 生成 [%s]", torrent_name)
            elif skip_nfo:
                logger.info("NFO 已预生成，跳过内联 NFO 生成 [%s]", torrent_name)
            else:
                for f in files:
                    is_sub = f.get("is_subtitle", False)
                    if is_sub:
                        continue

                    sub = _make_sub_for_path(f, series_name)
                    tvdb_ep = f.get("tvdb_episode") or 0
                    tmdb_ep = f.get("tmdb_episode") or 0

                    # Compute paths via template
                    rel_path = format_download_path(
                        template, sub,
                        tvdb_episode=tvdb_ep, tmdb_episode=tmdb_ep,
                    ).lstrip("/")
                    file_stem = Path(rel_path).stem
                    season_dir = Path(hardlink_root) / Path(rel_path).parent
                    show_dir = season_dir.parent
                    season_dir.mkdir(parents=True, exist_ok=True)

                    # tvshow.nfo (once per show_dir)
                    show_key = str(show_dir)
                    if show_key not in seen_show_dirs:
                        seen_show_dirs.add(show_key)
                        generate_tv_show_nfo(
                            title=f.get("tmdb_show_name", ""),
                            original_title=f.get("bangumi_show_name", ""),
                            plot="",
                            output_dir=str(show_dir),
                        )
                        nfo_generated += 1
                        logger.info("   tvshow.nfo → %s", show_dir / "tvshow.nfo")

                    # season.nfo (once per season_dir)
                    season_key = str(season_dir)
                    if season_key not in seen_season_dirs:
                        seen_season_dirs.add(season_key)
                        bgm_id = f.get("bangumi_id", 0)
                        tmdb_season = f.get("tmdb_season", 0)
                        generate_season_nfo(
                            title=f"Season {tmdb_season}",
                            original_title="",
                            plot="",
                            premiered="",
                            season_number=tmdb_season,
                            bangumi_id=bgm_id,
                            output_dir=str(season_dir),
                        )
                        nfo_generated += 1
                        logger.info("   season.nfo → %s", season_dir / "season.nfo")

                    # Episode NFO
                    await write_episode_files(
                        {},  # tmdb_ep (empty = skip thumb download)
                        season_number=f.get("tmdb_season", 0),
                        episode_number=f.get("tmdb_episode", 0),
                        bangumi_ep_id=f.get("bangumi_ep_id"),
                        show_name=f.get("tmdb_show_name", ""),
                        original_name=f.get("bangumi_show_name", ""),
                        bangumi_subject_name=f.get("bangumi_show_name", ""),
                        studios=[],
                        rating=0,
                        output_dir=str(season_dir),
                        thumb_source="tmdb",
                        file_stem=file_stem,
                    )
                    nfo_generated += 1
                    logger.info("   episode.nfo → %s", season_dir / f"{file_stem}.nfo")

            logger.info("下载后处理完成 [%s]: 创建了 %d 个文件, 生成了 %d 个 NFO", torrent_name, created, nfo_generated)

            # Remove task from tracker
            state._download_tasks.pop(info_hash, None)
            return

    logger.warning("下载监控超时 [%s] (24h)", torrent_name)
    state._download_tasks.pop(info_hash, None)


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
    series_name = _derive_series_name(preview_data)

    # ── Movie detection: check preview_data for movie content ──
    is_movie = False
    movie_meta: dict | None = None
    if preview_data:
        search_results = preview_data.get("search_results", {})
        for entry in search_results.values():
            if isinstance(entry, dict) and entry.get("media_type") == "movie":
                is_movie = True
                break

    # ── Generate NFO + images BEFORE resuming (if metadata provided) ──
    nfo_generated = False
    if preview_data:
        try:
            if is_movie:
                # ── Movie mode: extract metadata + generate movie.nfo ──
                movie_entry = next(
                    v for v in search_results.values()
                    if isinstance(v, dict) and v.get("media_type") == "movie"
                )
                tmdb_info = movie_entry.get("tmdb", {})
                tmdb_id = tmdb_info.get("id", 0)
                from ..services.nfo.generator import sanitize_path_name

                tmdb_name = sanitize_path_name(tmdb_info.get("name", "Unknown"))
                bangumi_ids = movie_entry.get("bangumi_ids", [])
                bangumi_id = bangumi_ids[0] if bangumi_ids else 0

                # Movie output path: {MOVIE_HARDLINK_PATH}/{tmdb_name}/
                movie_output_dir = Path(config.MOVIE_HARDLINK_PATH) / tmdb_name
                movie_output_dir.mkdir(parents=True, exist_ok=True)

                from ..services.nfo.nfo_xml import generate_movie_nfo
                nfo_path = generate_movie_nfo(
                    tmdb_id=tmdb_id,
                    bangumi_id=bangumi_id,
                    output_dir=str(movie_output_dir),
                )
                nfo_generated = True
                movie_meta = {
                    "tmdb_id": tmdb_id,
                    "tmdb_name": tmdb_name,
                    "bangumi_id": bangumi_id,
                }
                logger.info(
                    "预生成电影 NFO [%s]: %s (tmdb=%d, bangumi=%d)",
                    torrent_name, nfo_path, tmdb_id, bangumi_id,
                )
            else:
                # Build episode list for batch_nfo_generator
                nfo_episodes: list[dict] = []
                for f in files:
                    if f.get("is_subtitle"):
                        continue
                    # Resolve bangumi_subject_id from the file's bangumi_id
                    # (the search_result's bangumi.id for this show_name)
                    bgm_id = f.get("bangumi_id", 0)
                    nfo_episodes.append({
                        "bangumi_subject_id": bgm_id,
                        "bangumi_episode_sort": f.get("bangumi_sort", 0),
                        "tvdb_id": f.get("tvdb_season") and f.get("tvdb_episode") and _find_tvdb_id(preview_data, bgm_id) or 0,
                        "tvdb_season": f.get("tvdb_season"),     # None if not provided — 0 is valid (Specials)
                        "tvdb_episode": f.get("tvdb_episode"),   # None if not provided
                        "tmdb_id": _find_tmdb_id(preview_data, f.get("tmdb_show_name", "")),
                        "tmdb_season": f.get("tmdb_season", 0),
                        "tmdb_episode": f.get("tmdb_episode", 0),
                    })

                if nfo_episodes:
                    from ..services.nfo.generator import batch_nfo_generator
                    summary = await batch_nfo_generator(hardlink_root, nfo_episodes, series_name=series_name)
                    nfo_generated = True
                    logger.info(
                        "预生成元数据完成 [%s]: NFO=%d, images=%d",
                        torrent_name,
                        summary.get("nfoGenerated", 0),
                        summary.get("imagesDownloaded", 0),
                    )
        except Exception as e:
            logger.warning("预生成元数据失败 [%s]: %s — 将在下载完成后重试", torrent_name, e)

    # ── Resume download ──
    try:
        await resume_torrent(client, info_hash)
        logger.info("下载已恢复 [%s]", torrent_name)
    except Exception as e:
        raise HTTPException(500, f"恢复下载失败: {e}")

    # ── Start background monitor ──
    task = asyncio.create_task(
        _monitor_download(
            info_hash=info_hash,
            torrent_name=torrent_name,
            files=files,
            uploaded_subtitles=uploaded_subtitles,
            hardlink_root=config.MOVIE_HARDLINK_PATH if is_movie else hardlink_root,
            series_name=series_name,
            skip_nfo=nfo_generated,
            movie_meta=movie_meta,
        )
    )
    state._download_tasks[info_hash] = task

    # Clean up the temp torrent file (already added to qBittorrent)
    Path(torrent_path).unlink(missing_ok=True)

    return {
        "ok": True,
        "info_hash": info_hash,
        "message": f"种子已添加，选择性下载 {len(download_indices)}/{len(qb_files)} 个文件。下载完成后自动创建硬链接。",
    }


