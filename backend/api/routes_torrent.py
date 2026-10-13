"""API routes: /api/torrent/*."""

import logging
import os
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import data
from ..clients.errors import MissingAPIKeyError
from ..services.torrent import submission, queries
from ..utils.paths import SUBTITLE_DIR

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/api/torrent/collections")
async def list_torrent_collections():
    """Return poster cards per Bangumi subject, with tags shared by each torrent."""
    return await queries.list_collections()


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
    return await submission.submit_download(body)


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
    from ..services.metadata.providers.tmdb import build_season_episode_map
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
