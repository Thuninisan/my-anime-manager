"""API routes: /api/rss/download-history/*."""

import logging
import tempfile
from datetime import datetime
from pathlib import Path

import bencodepy
from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import config, data
from ..clients.qbittorrent import (
    delete_torrent,
    login as qb_login,
)
from ..services.metadata import refresh as nfo_refresh
from ..services.downloads.episode_submission import VIDEO_EXTENSIONS
from ..services.subscriptions import episode_download, episode_nfo
from ..utils.torrent_file_reader import read_torrent_file_list

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/api/rss/download-history/{bangumi_id}/regen-nfo", status_code=202)
async def refresh_subscription_nfo(bangumi_id: int):
    return nfo_refresh.start(bangumi_id)


@router.get("/api/rss/download-history/{bangumi_id}/regen-nfo")
async def subscription_nfo_status(bangumi_id: int):
    return nfo_refresh.latest(bangumi_id)


@router.get("/api/rss/nfo-refresh-tasks/{task_id}")
async def nfo_refresh_status(task_id: str):
    return nfo_refresh.get_task(task_id)

def _coordinate(value, field, *, integer=False):
    import math
    if (type(value) not in (int, float) or not math.isfinite(value) or value < 0 or
            (integer and int(value) != value)):
        raise HTTPException(422, f"invalid_{field}")
    return int(value) if integer or int(value) == value else value


# ── /api/rss/download-history/{bangumi_id}/{sort} ──

@router.delete("/api/rss/download-history/{bangumi_id}/{sort}")
async def delete_episode_history(bangumi_id: int, sort: int):
    """Remove a single episode from download history AND qBittorrent."""
    from ..db.mapping_changes import ensure_idle
    ensure_idle(bangumi_id)
    # Get info_hash before removing the record
    ep = data.get_all_episodes(bangumi_id).get(str(sort))
    info_hash = ep.get("info_hash", "") if ep else ""

    # Delete torrent from qBittorrent (with files)
    if info_hash:
        try:
            qb = await qb_login(
                config.QBITTORRENT_URL,
                config.QBITTORRENT_USERNAME,
                config.QBITTORRENT_PASSWORD,
            )
            await delete_torrent(qb, info_hash, delete_files=True)
            logger.info("deleted torrent from qBittorrent: hash=%s... files=True", info_hash[:12])
        except Exception:
            logger.exception("qBittorrent delete failed for hash=%s...", info_hash[:12])

    ok = data.remove_episode_record(bangumi_id, sort)
    if not ok:
        raise HTTPException(404, "记录不存在")
    return {"ok": True}


@router.post("/api/rss/download-history/{bangumi_id}/{sort}")
async def add_episode_history(bangumi_id: int, sort: int):
    """Manually mark a missing episode as downloaded (source='manual')."""
    from ..db.mapping_changes import ensure_idle
    ensure_idle(bangumi_id)
    data.mark_downloaded(
        bangumi_id, sort,
        guid="", source="manual", pub_date="", info_hash="",
    )
    return {"ok": True}


@router.post("/api/rss/download-history/{bangumi_id}/{sort}/upload")
async def upload_episode_torrent(bangumi_id: int, sort: int, file: UploadFile = File(...)):
    return await _submit_uploaded_episode(bangumi_id, sort, file, replace=False)


@router.post("/api/rss/download-history/{bangumi_id}/{sort}/replace")
async def replace_episode_torrent(bangumi_id: int, sort: int, file: UploadFile = File(...)):
    return await _submit_uploaded_episode(bangumi_id, sort, file, replace=True)


async def _submit_uploaded_episode(bangumi_id: int, sort: int, file: UploadFile, *, replace: bool):
    if not file.filename or not file.filename.lower().endswith(".torrent"):
        raise HTTPException(400, "Only .torrent files are accepted")
    tmp = tempfile.NamedTemporaryFile(suffix=".torrent", delete=False)
    try:
        tmp.write(await file.read())
        tmp.close()
        try:
            meta = bencodepy.decode(Path(tmp.name).read_bytes())
            torrent_name = meta[b"info"][b"name"].decode("utf-8", errors="replace")
            files = read_torrent_file_list(tmp.name)
            videos = [f for f in files if Path(f["name"]).suffix.lower() in VIDEO_EXTENSIONS]
            if len(videos) != 1:
                raise HTTPException(400, f"种子中视频文件数量不为1 (found {len(videos)})，请上传单集种子")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, "无效的 .torrent 文件") from exc
        info_hash = await episode_download.submit_episode(
            tmp.name, bangumi_id, sort, "edit" if replace else "add",
            guid=torrent_name,
            pub_date=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            replace_existing=replace,
        )
        return {"ok": True, "torrent_name": torrent_name, "info_hash": info_hash}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("单集种子提交失败: bangumi_id=%s sort=%s", bangumi_id, sort)
        raise HTTPException(500, f"单集种子提交失败: {exc}") from exc
    finally:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)


@router.patch("/api/rss/download-history/{bangumi_id}/{sort}", deprecated=True)
async def update_episode_overrides(
    bangumi_id: int, sort: int,
    fields: dict[str, object] = {},
    regen_nfo: bool = False,
):
    """Update the effective TMDB mapping using deprecated v1 field names.

    Body: ``{"tmdb_ep": 13, "tmdb_season": 2}`` — one or both fields.
    Query: ``?regen_nfo=true`` to regenerate NFO after setting overrides.
    """
    tmdb_ep = fields.get("tmdb_ep")
    tmdb_season = fields.get("tmdb_season")
    if tmdb_ep is None and tmdb_season is None:
        raise HTTPException(400, "至少需要提供 tmdb_ep 或 tmdb_season")
    if regen_nfo:
        nfo_refresh.ensure_idle(bangumi_id)

    ok = data.set_episode_mapping(
        bangumi_id, sort,
        tmdb_episode_number=_coordinate(tmdb_ep, "tmdb_episode_number") if tmdb_ep is not None else None,
        tmdb_season_number=_coordinate(tmdb_season, "tmdb_season_number", integer=True) if tmdb_season is not None else None,
    )
    if not ok:
        raise HTTPException(404, "该集的下载记录不存在")

    # ── Optional NFO regeneration (failures are logged, not raised, so the
    #    override write above still returns 200) ──
    if regen_nfo:
        try:
            async with nfo_refresh.write_lock:
                await episode_nfo.regen_episode_nfo(bangumi_id, sort)
        except Exception:
            logger.exception("overrides+PATCH: NFO regeneration failed")


@router.post("/api/rss/download-history/{bangumi_id}/{sort}/regen-nfo")
async def regen_episode_nfo(bangumi_id: int, sort: int):
    """Regenerate NFO using the stored episode mapping and media path.

    No request body — all inputs (subscription, per-episode mapping,
    paths) are derived server-side.  NFO-only, never touches qBittorrent.
    """
    from ..db.mapping_changes import ensure_idle
    ensure_idle(bangumi_id)
    nfo_refresh.ensure_idle(bangumi_id)
    try:
        async with nfo_refresh.write_lock:
            await episode_nfo.regen_episode_nfo(bangumi_id, sort)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("regen-nfo: unhandled error")
        raise HTTPException(500, f"NFO 重新生成失败: {e}")
    return {"ok": True}



@router.patch("/api/rss/v2/download-history/{bangumi_id}/{sort}")
async def update_canonical_episode_mapping(bangumi_id: int, sort: int,
                                           fields: dict[str, object], regen_nfo: bool = False):
    allowed = {"tmdb_episode_number", "tmdb_season_number"}
    if set(fields) - allowed:
        raise HTTPException(422, "invalid_episode_mapping_fields")
    return await update_episode_overrides(bangumi_id, sort,
        {"tmdb_ep": fields.get("tmdb_episode_number"), "tmdb_season": fields.get("tmdb_season_number")},
        regen_nfo)
