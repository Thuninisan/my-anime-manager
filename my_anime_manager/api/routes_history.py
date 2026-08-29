"""API routes: /api/rss/download-history/*."""

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

import bencodepy
from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import config, data
from ..clients.qbittorrent import (
    add_torrent,
    delete_torrent,
    get_torrent_files,
    login as qb_login,
    resume_torrent,
)
from ..services import downloader
from ..utils.torrent_file_reader import read_torrent_file_list
from ..utils.torrent_hash import compute_info_hash

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
    """Upload a .torrent file to manually add a missing episode.

    1. Parse torrent → extract name + info_hash
    2. Determine save path from subscription (same logic as RSS downloader)
    3. Add to qBittorrent (paused)
    4. Record in download_history.json (source='add')
    """
    if not file.filename or not file.filename.lower().endswith(".torrent"):
        raise HTTPException(400, "Only .torrent files are accepted")

    # ── Read subscription + resolve download dirs ──
    sub, season_dir, show_dir = downloader.resolve_episode_paths(bangumi_id, sort)
    show_name = sub.get("name", str(bangumi_id))
    series_name = sub.get("series_name") or show_name
    bgm_season = sub.get("bgm", {}).get("season", 1)
    tmdb_id = sub.get("tmdb", {}).get("id", 0)
    tmdb_season = sub.get("tmdb", {}).get("season")
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH

    # ── Save .torrent to temp file ──
    tmp = tempfile.NamedTemporaryFile(suffix=".torrent", delete=False)
    torrent_name = ""
    info_hash = ""
    try:
        contents = await file.read()
        tmp.write(contents)
        tmp.close()

        # ── Bencode parse → torrent name + info_hash ──
        with open(tmp.name, "rb") as f:
            meta = bencodepy.decode(f.read())
        info = meta[b"info"]
        torrent_name = info[b"name"].decode("utf-8", errors="replace")
        info_hash = compute_info_hash(tmp.name)
        logger.info("parsed torrent: name=%s hash=%s...", torrent_name, info_hash[:12])

        # ── Validate: exactly 1 video file ──
        VIDEO_EXTS = {".mkv", ".mp4", ".mka", ".avi", ".mov", ".ts", ".wmv", ".flv", ".webm"}
        file_list = read_torrent_file_list(tmp.name)
        logger.debug("torrent contains %d files", len(file_list))
        video_files = [
            f for f in file_list
            if Path(f["name"]).suffix.lower() in VIDEO_EXTS
        ]
        if len(video_files) != 1:
            logger.warning("rejected: %d video files (expected 1)", len(video_files))
            raise HTTPException(
                400,
                f"种子中视频文件数量不为1 (found {len(video_files)})，请上传单集种子",
            )

        # ── Add to qBittorrent (paused) ──
        logger.info("adding to qBittorrent: save_path=%s", rss_base)
        try:
            qb = await qb_login(
                config.QBITTORRENT_URL,
                config.QBITTORRENT_USERNAME,
                config.QBITTORRENT_PASSWORD,
            )
            add_hash = await add_torrent(qb, tmp.name, rss_base, torrent_name)
            logger.info("added torrent hash=%s...", add_hash[:12])
        except Exception as e:
            logger.exception("qBittorrent add failed")
            raise HTTPException(500, f"qBittorrent 添加失败: {e}")

        # ── Generate metadata + rename (same flow as RSS downloader) ──
        if tmdb_id:
            logger.info("generating metadata (tmdb_id=%d, season=%d)", tmdb_id, bgm_season)
            try:
                files = await get_torrent_files(qb, add_hash)
                old_path = files[0]["name"] if files else torrent_name
                await downloader.generate_metadata(
                    qb, add_hash, bangumi_id, sort,
                    bangumi_id,
                    tmdb_id, show_name,
                    old_path, torrent_name,
                    bgm_season=bgm_season,
                    tmdb_season=tmdb_season,
                    season_dir=season_dir,
                    show_dir=show_dir,
                    series_name=series_name,
                )
                logger.info("metadata generated")
            except Exception as e:
                logger.exception("NFO generation failed")
        else:
            logger.info("skipping metadata (no tmdb_id)")

        # ── Resume download ──
        logger.info("resuming torrent")
        try:
            await resume_torrent(qb, add_hash)
            logger.info("torrent resumed")
        except Exception:
            logger.warning("resume failed (non-fatal)", exc_info=True)

        # ── Record in download history ──
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        data.mark_downloaded(
            bangumi_id, sort,
            rss_url="",
            guid=torrent_name,
            source="add",
            pub_date=now,
            info_hash=info_hash,
        )
        logger.info("recorded in history (source=add, guid=%s)", torrent_name[:60])

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("unhandled error in upload")
        raise HTTPException(500, f"上传失败: {e}")

    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    return {"ok": True, "torrent_name": torrent_name, "info_hash": info_hash}


@router.post("/api/rss/download-history/{bangumi_id}/{sort}/replace")
async def replace_episode_torrent(bangumi_id: int, sort: int, file: UploadFile = File(...)):
    """Replace an existing episode with a new .torrent file.

    Deletes the old torrent from qBittorrent (with files), then follows
    the same flow as upload.  Records with source="edit".
    """
    if not file.filename or not file.filename.lower().endswith(".torrent"):
        raise HTTPException(400, "Only .torrent files are accepted")

    # ── Delete old torrent ──
    old_ep = data.get_all_episodes(bangumi_id).get(str(sort))
    if old_ep and old_ep.get("info_hash"):
        try:
            qb = await qb_login(
                config.QBITTORRENT_URL,
                config.QBITTORRENT_USERNAME,
                config.QBITTORRENT_PASSWORD,
            )
            await delete_torrent(qb, old_ep["info_hash"], delete_files=True)
            logger.info("replace: deleted old torrent hash=%s...", old_ep["info_hash"][:12])
        except Exception:
            logger.exception("replace: delete old torrent failed, continuing")

    # ── Read subscription + resolve download dirs ──
    sub, season_dir, show_dir = downloader.resolve_episode_paths(bangumi_id, sort)
    show_name = sub.get("name", str(bangumi_id))
    series_name = sub.get("series_name") or show_name
    bgm_season = sub.get("bgm", {}).get("season", 1)
    tmdb_id = sub.get("tmdb", {}).get("id", 0)
    tmdb_season = sub.get("tmdb", {}).get("season")
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH

    # ── Save .torrent to temp file ──
    tmp = tempfile.NamedTemporaryFile(suffix=".torrent", delete=False)
    torrent_name = ""
    info_hash = ""
    try:
        contents = await file.read()
        tmp.write(contents)
        tmp.close()

        # ── Bencode parse → torrent name + info_hash ──
        with open(tmp.name, "rb") as f:
            meta = bencodepy.decode(f.read())
        info = meta[b"info"]
        torrent_name = info[b"name"].decode("utf-8", errors="replace")
        info_hash = compute_info_hash(tmp.name)
        logger.info("replace: parsed torrent name=%s hash=%s...", torrent_name, info_hash[:12])

        # ── Validate: exactly 1 video file ──
        VIDEO_EXTS = {".mkv", ".mp4", ".mka", ".avi", ".mov", ".ts", ".wmv", ".flv", ".webm"}
        file_list = read_torrent_file_list(tmp.name)
        video_files = [f for f in file_list if Path(f["name"]).suffix.lower() in VIDEO_EXTS]
        if len(video_files) != 1:
            raise HTTPException(400, f"种子中视频文件数量不为1 (found {len(video_files)})")

        # ── Add to qBittorrent (paused) ──
        try:
            qb = await qb_login(
                config.QBITTORRENT_URL,
                config.QBITTORRENT_USERNAME,
                config.QBITTORRENT_PASSWORD,
            )
            add_hash = await add_torrent(qb, tmp.name, rss_base, torrent_name)
            logger.info("replace: added torrent hash=%s...", add_hash[:12])
        except Exception as e:
            raise HTTPException(500, f"qBittorrent 添加失败: {e}")

        # ── Generate metadata + rename ──
        if tmdb_id:
            try:
                files = await get_torrent_files(qb, add_hash)
                old_path = files[0]["name"] if files else torrent_name
                await downloader.generate_metadata(
                    qb, add_hash, bangumi_id, sort,
                    bangumi_id, tmdb_id, show_name,
                    old_path, torrent_name,
                    bgm_season=bgm_season, tmdb_season=tmdb_season,
                    season_dir=season_dir, show_dir=show_dir,
                    series_name=series_name,
                )
                logger.info("replace: metadata generated")
            except Exception as e:
                logger.exception("replace: NFO generation failed")

        # ── Resume download ──
        try:
            await resume_torrent(qb, add_hash)
        except Exception:
            pass

        # ── Record in download history (source="edit") ──
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        data.mark_downloaded(
            bangumi_id, sort,
            rss_url="",
            guid=torrent_name,
            source="edit",
            pub_date=now,
            info_hash=info_hash,
        )
        logger.info("replace: recorded (source=edit)")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("unhandled error in replace")
        raise HTTPException(500, f"替换失败: {e}")

    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    return {"ok": True, "torrent_name": torrent_name, "info_hash": info_hash}


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

    No request body — all inputs (subscription, info_hash, per-episode
    overrides, paths) are derived server-side.
    """
    try:
        await downloader.regen_episode_nfo(bangumi_id, sort)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("regen-nfo: unhandled error")
        raise HTTPException(500, f"NFO 重新生成失败: {e}")
    return {"ok": True}


