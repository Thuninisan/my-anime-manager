"""API routes: /api/torrent/*."""

import asyncio
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import config, data
from ..db import torrents as torrent_store
from ..db import recovery
from ..services.workflow_recovery import execution
from . import state
from ..clients import bangumi as bgm_client
from ..clients.errors import MissingAPIKeyError
from ..clients.qbittorrent import (
    add_torrent,
    get_torrent_files,
    get_torrents_by_hashes,
    login as qb_login,
    resume_torrent,
    set_file_priority,
)
from ..services import bd_replacement, rss_poster
from ..services.nfo import format_download_path
from ..utils.torrent_hash import compute_info_hash
from ..services.torrent.monitor import build_processing, monitor_download, monitor_processing
from ..services.torrent import fontinass
from ..logging.logging_config import operation_context
from ..services.torrent.metadata import pre_generate_nfo
from ..services.torrent.preview import derive_series_name
from ..utils.paths import SUBTITLE_DIR
from ..utils.torrent_file_reader import read_torrent_file_list

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/api/torrent/collections")
async def list_torrent_collections():
    """Return poster cards per Bangumi subject, with tags shared by each torrent."""
    cards = []
    titles: dict[int, str] = {}
    for record in reversed(torrent_store.list_torrents()):
        tags = []
        if record.get("encoding_group") == "ktnbytes":
            tags.append("ktnbytes")
        codec = (record.get("video_codec") or "").lower()
        if codec in {"av1", "h265"}:
            tags.append("AV1" if codec == "av1" else "H.265")
        bangumi_ids = list(dict.fromkeys(
            bid for bid in record.get("bangumi_ids", [])
            if isinstance(bid, int) and not isinstance(bid, bool) and bid > 0
        ))
        for bangumi_id in bangumi_ids or [None]:
            if bangumi_id is not None and bangumi_id not in titles:
                titles[bangumi_id] = data.get_bangumi_name(bangumi_id) or ""
            cards.append({
                "card_key": f"{record['info_hash']}:{bangumi_id if bangumi_id is not None else 'unmatched'}",
                "bangumi_id": bangumi_id,
                "title_cn": titles.get(bangumi_id, ""),
                "poster_url": rss_poster.poster_url(bangumi_id) if bangumi_id is not None else "",
                "tags": tags,
                "info_hash": record["info_hash"],
            })
    return cards


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


def _start_torrent_monitor(record: dict, context: dict | None = None) -> None:
    info_hash = record["info_hash"]
    existing = state._download_tasks.get(info_hash)
    if existing and not existing.done():
        return

    async def run_work(job: recovery.RecoveryJob) -> None:
        processing = job.checkpoint["processing"]
        if job.stage in {"wait_download", "organize_files"}:
            job.save(stage="organize_files")
            if context is None:
                linked = await monitor_processing(info_hash, record["torrent_name"], processing)
            else:
                linked = await monitor_download(
                    info_hash=info_hash,
                    torrent_name=context["torrent_name"],
                    files=context["files"],
                    uploaded_subtitles=context["uploaded_subtitles"],
                    hardlink_root=context["hardlink_root"],
                    series_name=context["series_name"],
                    skip_nfo=context["skip_nfo"],
                    movie_meta=context["movie_meta"],
                )
            required = sum(f["action"] == "hardlink" for f in processing["files"])
            success = linked is not None and len(linked) == required
            if success and processing.get("replace_bangumi_id") is not None:
                bangumi_id = processing["replace_bangumi_id"]
                mapping = [
                    {"episode_mapping": item["episode_mapping_snapshot"]["episode_mapping"], "is_subtitle": False}
                    for item in processing["files"] if item["action"] == "hardlink"
                ]
                episodes = bd_replacement.validate_mapping(bangumi_id, mapping)
                client = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
                hashes, old_links = await bd_replacement.old_files(client, bangumi_id, episodes, info_hash)
                success = await bd_replacement.finalize(client, bangumi_id, episodes, hashes, old_links, linked, required)
            job.checkpoint["media_status"] = "completed" if success else "failed"
            job.save(stage="fontinass" if linked is not None else "finalize")

        if job.stage == "fontinass":
            # Font processing does not hold back the completed media result.
            torrent_store.finish_torrent(info_hash, job.checkpoint["media_status"],
                                        job=job, keep_recovery=True)
            if not await fontinass.process(info_hash):
                job.release(blocked=True)
                return
            job.checkpoint.pop("fontinass", None)
            job.save(stage="finalize")

        if job.stage != "finalize":
            raise ValueError("unsupported_torrent_recovery_stage")
        torrent_store.finish_torrent(info_hash, job.checkpoint["media_status"], job=job)

    async def run() -> None:
        job = None
        try:
            job = recovery.RecoveryJob.claim("torrent_processing", info_hash)
            if job is None:
                return
            with operation_context(job.id):
                logger.info("workflow.start torrent=%s stage=%s mode=%s", info_hash,
                            job.stage, "recovery" if context is None else "normal")
                async with execution(job):
                    await run_work(job)
        except recovery.LostLease:
            logger.exception("Torrent 恢复任务失去执行租约: %s", info_hash)
        except asyncio.CancelledError:
            if job is not None:
                try:
                    job.release()
                except recovery.LostLease:
                    pass
            raise
        except Exception:
            logger.exception("Torrent 后处理异常，保留恢复检查点: %s", info_hash)
            if job is not None:
                try:
                    job.release(delay_seconds=60)
                except recovery.LostLease:
                    pass
        finally:
            state._download_tasks.pop(info_hash, None)

    state._download_tasks[info_hash] = asyncio.create_task(run())


async def recover_torrent_monitors() -> None:
    """Resume each unfinished workflow from its shared checkpoint stage."""
    pending = torrent_store.list_pending_torrents()
    download_jobs = []
    for record in pending:
        info_hash = record["info_hash"]
        if info_hash in state._download_tasks:
            continue
        with recovery.new_session() as session:
            row = recovery.read(session, "torrent_processing", info_hash)
            stage = row.stage if row else None
            blocked = row is not None and row.state == "blocked"
        if blocked and not config.FONTINASS_ENABLED:
            continue
        if stage in {"fontinass", "finalize"}:
            _start_torrent_monitor(record)
        else:
            download_jobs.append(record)
    if not download_jobs:
        return
    try:
        client = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
        torrents = await get_torrents_by_hashes(client, [record["info_hash"] for record in download_jobs], raise_on_error=True)
    except Exception:
        logger.exception("Torrent 监控恢复延迟：qBittorrent 不可用")
        return
    for record in download_jobs:
        info_hash = record["info_hash"]
        if info_hash in state._download_tasks:
            continue
        if info_hash in torrents:
            _start_torrent_monitor(record)
        else:
            job = recovery.RecoveryJob.claim("torrent_processing", info_hash)
            if job is not None:
                logger.warning("Torrent 已从 qBittorrent 移除，结束恢复任务: %s", info_hash)
                torrent_store.finish_torrent(info_hash, "failed", job=job)


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
        Versioned preview with parsed files, scoped provider candidates and
        episode catalogs. Search never confirms a resource identity.
    """
    if not file.filename or not file.filename.endswith(".torrent"):
        raise HTTPException(400, "请上传 .torrent 文件")

    # Save uploaded file to temp location
    with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        from ..services.torrent.preview import parse_and_search
        result = await parse_and_search(tmp_path)
    except MissingAPIKeyError:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    except Exception as e:
        Path(tmp_path).unlink(missing_ok=True)
        logger.exception("种子预览解析失败")
        raise HTTPException(400, str(e))

    from ..services.torrent.preview_session import create_preview_session
    from ..services.torrent.preview_view import session_view
    try:
        row = create_preview_session(result, tmp_path)
        return session_view(row)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── /api/torrent/bangumi/{id}/episodes ──

@router.get("/api/torrent/bangumi/{bangumi_id}/episodes", deprecated=True)
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
            "ep": ep.get("ep"),
            "raw_sort": ep.get("sort"),
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

    The immutable canonical preview snapshot supplies NFO
    files and images generated **before** the torrent is resumed,
    matching the batch/scan flow behaviour.
    """
    if "preview_id" not in body:
        raise HTTPException(409, "preview_schema_outdated: create a canonical preview")
    from ..services.torrent.preview_session import restore_download_request
    body = restore_download_request(body)
    torrent_path = body.get("torrent_path", "")
    resource_id = body.get("resource_id")
    if resource_id is not None:
        from ..db import resources as resource_store
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
    torrent_name = body.get("torrent_name", "")
    files: list[dict] = body.get("files", [])
    uploaded_subtitles: list[dict] = body.get("uploaded_subtitles", [])
    preview_snapshot = body["preview_snapshot"]
    replace_bangumi_id = body.get("replace_bangumi_id")

    if not torrent_path or not Path(torrent_path).is_file():
        raise HTTPException(400, "种子文件不存在")
    if not files:
        raise HTTPException(400, "文件列表为空")
    if replace_bangumi_id is not None:
        try:
            replace_bangumi_id = int(replace_bangumi_id)
        except (TypeError, ValueError):
            raise HTTPException(400, "无效的订阅 ID")
        episodes = bd_replacement.validate_mapping(replace_bangumi_id, files)

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
    try:
        torrent_store.save_torrent(record)
    except Exception as e:
        logger.exception("保存 Torrent 记录失败: %s", info_hash)
        raise HTTPException(500, f"种子已开始下载，但保存记录失败: {e}")
    _start_torrent_monitor(record, context)

    # Clean up the temp torrent file (already added to qBittorrent)
    if resource_id is None:
        Path(torrent_path).unlink(missing_ok=True)

    return {
        "ok": True,
        "info_hash": info_hash,
        "message": f"种子已添加，选择性下载 {len(download_indices)}/{len(qb_files)} 个文件。下载完成后自动创建硬链接。",
    }


@router.get("/api/torrent/previews/{preview_id}")
async def get_preview(preview_id: str):
    from ..services.torrent.preview_session import load_preview_session, touch_preview_session
    from ..services.torrent.preview_view import build_preview_view
    row, snapshot = load_preview_session(preview_id)
    row = touch_preview_session(row)
    return build_preview_view(snapshot, row.id, row.revision, row.expires_at)


@router.post("/api/torrent/previews/{preview_id}/augment")
async def augment_preview(preview_id: str, body: dict):
    from ..services.torrent.preview_session import augment_preview_session
    provider = body.get("provider")
    provider_id = body.get("provider_id", body.get("subject_id" if provider == "bangumi" else "series_id"))
    if type(provider_id) is not int or provider_id <= 0 or type(body.get("preview_revision")) is not int:
        raise HTTPException(422, "invalid_preview_augment")
    return await augment_preview_session(preview_id, body["preview_revision"], body.get("show_key", ""), provider, provider_id)


@router.post("/api/torrent/previews/{preview_id}/remove-candidate")
async def remove_candidate(preview_id: str, body: dict):
    from ..services.torrent.preview_session import remove_preview_candidate
    if type(body.get("preview_revision")) is not int or type(body.get("provider_id")) is not int:
        raise HTTPException(422, "invalid_preview_candidate")
    return remove_preview_candidate(preview_id, body["preview_revision"], body.get("show_key", ""), body.get("provider"), body["provider_id"])


@router.get("/api/torrent/catalogs/tmdb/{series_id}")
async def tmdb_catalog_view(series_id: int):
    from ..domain.episode_adapters import episode_catalog
    from ..services.tmdb import build_season_episode_map
    from ..clients import tmdb
    seasons = await build_season_episode_map(series_id)
    detail = (await tmdb.get_tv_detail(series_id)).json()
    return {"name": detail.get("name") or str(series_id),
            "seasons": episode_catalog({"tmdb": {str(series_id): seasons}})["tmdb"][str(series_id)]}


@router.get("/api/torrent/catalogs/bangumi/{subject_id}")
async def bangumi_catalog_view(subject_id: int):
    from ..domain.episode_adapters import episode_catalog
    # The legacy endpoint is a provider normalization boundary; its raw shape
    # never enters the matcher contract.
    raw = await torrent_bangumi_episodes(subject_id)
    return episode_catalog({"bangumi": {str(subject_id): raw}})["bangumi"][str(subject_id)]


@router.post("/api/torrent/previews/{preview_id}/match-source")
async def change_preview_match_source(preview_id: str, body: dict):
    from ..services.torrent.preview_session import set_preview_match_source
    if type(body.get("preview_revision")) is not int:
        raise HTTPException(422, "invalid_preview_revision")
    return set_preview_match_source(preview_id, body["preview_revision"], body.get("source"))
