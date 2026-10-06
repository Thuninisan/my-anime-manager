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
from ..services import downloader
from ..utils.torrent_file_reader import read_torrent_file_list

router = APIRouter()
logger = logging.getLogger(__name__)

# ── /api/rss/download-history/{bangumi_id}/{sort} ──

@router.delete("/api/rss/download-history/{bangumi_id}/{sort}")
async def delete_episode_history(bangumi_id: int, sort: int):
    """Remove a single episode from download history AND qBittorrent."""
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
    data.mark_downloaded(
        bangumi_id, sort,
        rss_url="", guid="", source="manual", pub_date="", info_hash="",
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
    sub, _, _ = downloader.resolve_episode_paths(bangumi_id, sort)
    tmp = tempfile.NamedTemporaryFile(suffix=".torrent", delete=False)
    try:
        tmp.write(await file.read())
        tmp.close()
        try:
            meta = bencodepy.decode(Path(tmp.name).read_bytes())
            torrent_name = meta[b"info"][b"name"].decode("utf-8", errors="replace")
            files = read_torrent_file_list(tmp.name)
            videos = [f for f in files if Path(f["name"]).suffix.lower() in downloader.VIDEO_EXTENSIONS]
            if len(videos) != 1:
                raise HTTPException(400, f"种子中视频文件数量不为1 (found {len(videos)})，请上传单集种子")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, "无效的 .torrent 文件") from exc
        info_hash = await downloader.submit_episode_torrent(
            tmp.name, bangumi_id, sort, "edit" if replace else "add",
            sub=sub, guid=torrent_name,
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


@router.patch("/api/rss/download-history/{bangumi_id}/{sort}")
async def update_episode_overrides(
    bangumi_id: int, sort: int,
    fields: dict[str, object] = {},
    regen_nfo: bool = False,
):
    """Set TMDB overrides for an episode and optionally regenerate NFO.

    Body: ``{"tmdb_ep": 13, "tmdb_season": 2}`` — one or both fields.
    Query: ``?regen_nfo=true`` to regenerate NFO after setting overrides.
    """
    tmdb_ep = fields.get("tmdb_ep")
    tmdb_season = fields.get("tmdb_season")
    if tmdb_ep is None and tmdb_season is None:
        raise HTTPException(400, "至少需要提供 tmdb_ep 或 tmdb_season")

    ok = data.set_episode_overrides(
        bangumi_id, sort,
        tmdb_ep=int(tmdb_ep) if tmdb_ep is not None else None,
        tmdb_season=int(tmdb_season) if tmdb_season is not None else None,
    )
    if not ok:
        raise HTTPException(404, "该集的下载记录不存在")

    # ── Optional NFO regeneration (failures are logged, not raised, so the
    #    override write above still returns 200) ──
    if regen_nfo:
        try:
            await downloader.regen_episode_nfo(bangumi_id, sort)
        except Exception:
            logger.exception("overrides+PATCH: NFO regeneration failed")


@router.post("/api/rss/download-history/{bangumi_id}/{sort}/regen-nfo")
async def regen_episode_nfo(bangumi_id: int, sort: int):
    """Regenerate NFO for a single episode using its stored TMDB overrides.

    No request body — all inputs (subscription, per-episode overrides,
    paths) are derived server-side.  NFO-only, never touches qBittorrent.
    """
    try:
        await downloader.regen_episode_nfo(bangumi_id, sort)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("regen-nfo: unhandled error")
        raise HTTPException(500, f"NFO 重新生成失败: {e}")
    return {"ok": True}

