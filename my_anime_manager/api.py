"""FastAPI server for My Anime Manager.

Endpoints:
    POST /api/torrent/preview       — upload .torrent → return full preview JSON
    POST /api/torrent/confirm       — accept (modified) preview JSON → execute
    POST /scan                      — scan directory in background
    GET  /scan/status               — scan progress
    GET  /watch/status              — watch loop status
    GET  /config                    — read config
    PUT  /config                    — update config

Usage:
    uvicorn my_anime_manager.api:app --host 0.0.0.0 --port 8000
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .services.batch_service import build_preview, execute_confirm, process_torrent
from .services import rss as rss_service
from .services import downloader
from .services import image_downloader as image_service
from .clients.qbittorrent import login as qb_login, get_torrents_by_hashes
from . import data

app = FastAPI(
    title="My Anime Manager",
    description="TMDB + Bangumi 联动工具，为 Jellyfin 生成 NFO 元数据，支持 qBittorrent",
    version="1.0.0",
)

# ═══════════════════════════════════════════════════════════════════════
# CORS — allow frontend dev servers
# ═══════════════════════════════════════════════════════════════════════

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ═══════════════════════════════════════════════════════════════════════
# Static file serving — only when frontend build exists (production)
# ═══════════════════════════════════════════════════════════════════════

_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"


@app.on_event("startup")
async def on_startup():
    """Mount static files after all routes are registered (production mode)."""
    if _frontend_dist.exists() and _frontend_dist.is_dir():
        # Mount static assets at /assets/
        assets_dir = _frontend_dist / "assets"
        if assets_dir.exists():
            app.mount(
                "/assets",
                StaticFiles(directory=str(assets_dir)),
                name="frontend-assets",
            )

    # Auto-start directory watcher if WATCH_DIR env var is set
    watch_dir = os.environ.get("WATCH_DIR", "")
    if watch_dir:
        global _watch_task
        _watch_task = asyncio.create_task(_watch_worker(watch_dir))


# ═══════════════════════════════════════════════════════════════════════
# Pydantic response models
# ═══════════════════════════════════════════════════════════════════════

class EpisodePreview(BaseModel):
    fileName: str
    torrentPath: str
    showName: str
    season: int
    episode: int


class ExtraPreview(BaseModel):
    fileName: str
    torrentPath: str
    type: str  # "oped" | "special" | "unknown"


class TmdbEpisodeInfo(BaseModel):
    epNum: int
    name: str
    tmdbId: int
    overview: str = ""
    airDate: str = ""
    runtime: int = 0
    stillPath: str = ""


class SeasonInfo(BaseModel):
    name: str
    episodes: list[TmdbEpisodeInfo]


class TmdbPreview(BaseModel):
    id: int
    name: str
    originalName: str = ""
    firstAirDate: str = ""
    overview: str = ""
    genres: list[str] = []
    studios: list[str] = []
    numSeasons: int = 0
    posterPath: str = ""
    backdropPath: str = ""
    voteAverage: float = 0.0
    status: str = ""
    seasonMap: dict[str, SeasonInfo] = {}


class BangumiEntryPreview(BaseModel):
    id: int
    name: str
    nameCn: str | None = None
    date: str | None = None
    eps: int = 0


class BangumiPreview(BaseModel):
    chain: list[BangumiEntryPreview]
    startEntryId: int


class FileMappingPreview(BaseModel):
    oldPath: str
    newPath: str
    type: str  # "episode" | "oped" | "special" | "unknown"


class EpisodeMatchPreview(BaseModel):
    """Per-episode matching detail for the frontend."""
    fileName: str
    torrentPath: str
    showName: str
    season: int
    episode: int
    seasonNumber: int
    episodeNumber: int
    bangumiSubjectName: str
    bangumiEpId: int | None = None
    tmdbEpName: str = ""
    tmdbEpId: int = 0


class ConfirmResponse(BaseModel):
    ok: bool
    nfoGenerated: int = 0
    imagesDownloaded: int = 0
    filesRenamed: int = 0
    showDirName: str = ""
    error: str = ""


class RssSubtitleGroup(BaseModel):
    name: str
    subgroup_id: int
    rss_url: str


class BangumiRssResponse(BaseModel):
    bangumi_id: int
    name: str
    mikan_id: int
    global_rss: str
    groups: list[RssSubtitleGroup]


class RssFeedItem(BaseModel):
    guid: str
    title: str
    torrent_url: str
    pub_date: str
    size_bytes: int
    downloaded: bool
    tags: list[str]
    passed: bool
    excluded: bool
    episode_number: int = 0


class RssFeedResponse(BaseModel):
    title: str
    items: list[RssFeedItem]


class SubscriptionIn(BaseModel):
    name: str
    rss_url: str
    bangumi_id: int
    subgroup_id: int
    subgroup_name: str
    filter_tags: list[str] = []
    backup_rss_url: str = ""
    backup_subgroup_id: int = 0
    backup_subgroup_name: str = ""
    backup_filter_tags: list[str] = []
    download_path: str = ""
    active: int = 1


class SubscriptionOut(BaseModel):
    name: str
    rss_url: str
    bangumi_id: int
    subgroup_id: int
    subgroup_name: str
    filter_tags: list[str] = []
    backup_rss_url: str = ""
    backup_subgroup_id: int = 0
    backup_subgroup_name: str = ""
    backup_filter_tags: list[str] = []
    created_at: str = ""
    updated_at: str = ""
    download_path: str = ""
    active: int = 1
    # Pre-computed season metadata (from Bangumi chain)
    bgm_season: int = 1
    bgm_sortrange: list[int] = []
    tmdb_id: int = 0
    tmdb_season: int | None = None
    # Poster image URL (Bangumi CDN URL, frontend loads directly)
    poster_url: str = ""
    # Downloaded episode count (from download_history.json)
    downloaded_count: int = 0


class ScanStatus(BaseModel):
    running: bool
    dir: str
    total: int
    processed: int
    deleted: int
    failed: int
    current_file: str
    errors: list[str]


# ═══════════════════════════════════════════════════════════════════════
# Helpers: convert internal dicts to Pydantic models
# ═══════════════════════════════════════════════════════════════════════

def _make_tmdb_preview(tv_show: dict, detail: dict, season_map: dict) -> TmdbPreview:
    """Build a TmdbPreview from raw service-layer dicts."""
    seasons: dict[str, SeasonInfo] = {}
    for sk, sv in season_map.items():
        eps = [
            TmdbEpisodeInfo(
                epNum=e["epNum"],
                name=e["name"],
                tmdbId=e["tmdbId"],
                overview=e.get("overview", ""),
                airDate=e.get("airDate", ""),
                runtime=e.get("runtime", 0),
                stillPath=e.get("stillPath", ""),
            )
            for e in sv.get("episodes", [])
        ]
        seasons[str(sk)] = SeasonInfo(name=sv.get("name", f"Season {sk}"), episodes=eps)

    return TmdbPreview(
        id=tv_show["id"],
        name=tv_show["name"],
        originalName=detail.get("original_name") or tv_show.get("original_name", "") or "",
        firstAirDate=detail.get("first_air_date", ""),
        overview=detail.get("overview", ""),
        genres=detail.get("genres", []),
        studios=detail.get("studios", []),
        numSeasons=detail.get("number_of_seasons", 0),
        posterPath=detail.get("poster_path", ""),
        backdropPath=detail.get("backdrop_path", ""),
        voteAverage=detail.get("vote_average", 0.0),
        status=detail.get("status", ""),
        seasonMap=seasons,
    )


def _make_bangumi_preview(chain: list[dict], start_entry_id: int) -> BangumiPreview:
    """Build a BangumiPreview from the chain list."""
    entries = [
        BangumiEntryPreview(
            id=e["id"],
            name=e["name"],
            nameCn=e.get("name_cn"),
            date=e.get("date"),
            eps=e.get("eps", 0),
        )
        for e in chain
    ]
    return BangumiPreview(chain=entries, startEntryId=start_entry_id)


# ═══════════════════════════════════════════════════════════════════════
# background scan state
# ═══════════════════════════════════════════════════════════════════════

_scan_task: Optional[asyncio.Task] = None
_scan_status: dict = {
    "running": False,
    "dir": "",
    "total": 0,
    "processed": 0,
    "deleted": 0,
    "failed": 0,
    "current_file": "",
    "errors": [],
}


async def _scan_worker(dir_path: str):
    """Background worker: scan directory and process each .torrent file."""
    global _scan_status
    abs_dir = Path(dir_path).resolve()

    _scan_status = {
        "running": True,
        "dir": str(abs_dir),
        "total": 0,
        "processed": 0,
        "deleted": 0,
        "failed": 0,
        "current_file": "",
        "errors": [],
    }

    if not abs_dir.exists():
        _scan_status["errors"].append(f"目录不存在: {abs_dir}")
        _scan_status["running"] = False
        return

    files = sorted(abs_dir.glob("*.torrent"))
    _scan_status["total"] = len(files)

    if not files:
        _scan_status["running"] = False
        return

    for file in files:
        _scan_status["current_file"] = file.name
        try:
            await process_torrent(str(file))
            file.unlink()
            _scan_status["processed"] += 1
            _scan_status["deleted"] += 1
        except Exception as e:
            _scan_status["failed"] += 1
            _scan_status["errors"].append(f"{file.name}: {e}")

    _scan_status["current_file"] = ""
    _scan_status["running"] = False


# ═══════════════════════════════════════════════════════════════════════
# background watch state
# ═══════════════════════════════════════════════════════════════════════

_watch_task: Optional[asyncio.Task] = None
_watch_status: dict = {
    "running": False,
    "dir": "",
    "processed": 0,
    "deleted": 0,
    "failed": 0,
    "current_file": "",
    "errors": [],
}


async def _watch_worker(dir_path: str):
    """Background worker: continuously watch directory for .torrent files."""
    global _watch_status
    abs_dir = Path(dir_path).resolve()

    if not abs_dir.exists():
        print(f"❌ 监控目录不存在: {abs_dir}")
        _watch_status["running"] = False
        return

    SCAN_INTERVAL = 30
    print(f"👀 开始监控 {abs_dir}，每 {SCAN_INTERVAL}s 扫描一次...")

    _watch_status["running"] = True
    _watch_status["dir"] = str(abs_dir)

    while True:
        files = sorted(abs_dir.glob("*.torrent"))

        if files:
            print(f"📁 扫描到 {len(files)} 个 torrent 文件")
            _watch_status["errors"] = []

            failed_dir = abs_dir / "failed"
            failed_dir.mkdir(exist_ok=True)

            for file in files:
                _watch_status["current_file"] = file.name
                success = False
                try:
                    await process_torrent(str(file))
                    success = True
                except Exception as e:
                    _watch_status["failed"] += 1
                    _watch_status["errors"].append(f"{file.name}: {e!r}")
                    print(f"❌ 处理失败 {file.name}: {e!r}")
                    traceback.print_exc()

                if not file.exists():
                    _watch_status["processed"] += 1
                    _watch_status["deleted"] += 1
                elif success:
                    file.unlink()
                    _watch_status["processed"] += 1
                    _watch_status["deleted"] += 1
                else:
                    dest = failed_dir / file.name
                    if dest.exists():
                        stem = file.stem
                        counter = 1
                        while dest.exists():
                            dest = failed_dir / f"{stem}_{counter}.torrent"
                            counter += 1
                    file.rename(dest)
                    print(f"   ⚠️ 处理失败，已移到 {dest}")

            _watch_status["current_file"] = ""
            print("   继续监控...")

        await asyncio.sleep(SCAN_INTERVAL)


# ═══════════════════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    """Health check + status overview, or serve the frontend."""
    # If frontend is built, serve index.html
    index_path = _frontend_dist / "index.html"
    if index_path.exists():
        from fastapi.responses import FileResponse
        return FileResponse(str(index_path))

    return {
        "service": "My Anime Manager",
        "version": "1.0.0",
        "docs": "/docs",
        "watch": {
            "running": _watch_status["running"],
            "dir": _watch_status["dir"],
            "processed": _watch_status["processed"],
            "failed": _watch_status["failed"],
        },
    }


# ── /api/torrent/preview ──

@app.post("/api/torrent/preview")
async def torrent_preview(file: UploadFile = File(...)):
    """Upload a .torrent file for preview analysis.

    Returns the full preview JSON — the frontend may modify it before
    posting it back to ``/api/torrent/confirm``.
    """
    if not file.filename or not file.filename.endswith(".torrent"):
        raise HTTPException(400, "请上传 .torrent 文件")

    # Save uploaded file to temp location
    with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        preview_data = await build_preview(tmp_path)
    except Exception as e:
        Path(tmp_path).unlink(missing_ok=True)
        traceback.print_exc()
        raise HTTPException(400, str(e))

    # build_preview already returns the new {tvshow, seasons, episodes} format
    return preview_data


# ── /api/torrent/confirm ──

@app.post("/api/torrent/confirm", response_model=ConfirmResponse)
async def torrent_confirm(body: dict):
    """Execute the confirmed plan.

    Accepts the (possibly modified) preview JSON and performs all writes:
    NFO generation, image downloads, qBittorrent renames, and torrent resume.
    """
    torrent_path = body.get("torrent_path", "")
    summary = await execute_confirm(body)

    # Clean up the temp .torrent file
    if torrent_path:
        Path(torrent_path).unlink(missing_ok=True)

    return ConfirmResponse(
        ok=not summary.get("error"),
        nfoGenerated=summary.get("nfoGenerated", 0),
        imagesDownloaded=summary.get("imagesDownloaded", 0),
        filesRenamed=summary.get("filesRenamed", 0),
        showDirName=summary.get("showDirName", ""),
        error=summary.get("error", ""),
    )


# ── /scan ──

@app.post("/scan")
async def start_scan(dir_path: str = Form(...)):
    """Start scanning a directory for .torrent files in the background.

    Only one scan can run at a time.
    """
    global _scan_task
    if _scan_task and not _scan_task.done():
        raise HTTPException(409, "扫描任务已在运行中")

    _scan_task = asyncio.create_task(_scan_worker(dir_path))
    return {"ok": True, "dir": dir_path, "message": "扫描已启动"}


@app.get("/scan/status", response_model=ScanStatus)
async def scan_status():
    """Get the current scan progress."""
    return ScanStatus(**_scan_status)


@app.get("/watch/status")
async def watch_status():
    """Get the current watch loop status (auto-started via WATCH_DIR)."""
    return _watch_status


# ── /config ──

@app.get("/config")
async def get_config():
    """Read all current config values (sensitive fields masked)."""
    return config.get_all()


@app.put("/config")
async def update_config(changes: dict[str, object]):
    """Update config values at runtime.

    Example body:
        {"TMDB_API_KEY": "new-key", "PROXY_PORT": 1080}

    Only known config keys are accepted; unknown keys are silently ignored.
    """
    return config.update(changes)


# ── /api/rss/bangumi/{id} ──

@app.get("/api/rss/bangumi/{bangumi_id}", response_model=BangumiRssResponse)
async def get_bangumi_rss(bangumi_id: int):
    """Look up Mikan subtitle groups and their RSS URLs for a Bangumi subject ID.

    Maps Bangumi subject ID → Mikan ID via bangumi-data, then scrapes the
    Mikan page to extract all subtitle groups and their RSS feed URLs.
    """
    result = await rss_service.lookup_bangumi_rss(bangumi_id)
    if result is None:
        raise HTTPException(404, f"未找到 Bangumi ID {bangumi_id} 对应的 Mikan 条目")
    return BangumiRssResponse(**result)


@app.get("/api/rss/data-status")
async def rss_data_status():
    """Check whether the bangumi-data mapping file exists."""
    from .data import _MAP_FILE
    exists = _MAP_FILE.exists()
    count = 0
    if exists:
        import json
        try:
            raw = json.loads(_MAP_FILE.read_text(encoding="utf-8"))
            count = len(raw)
        except Exception:
            pass
    return {"exists": exists, "count": count}


@app.post("/api/rss/download-data")
async def rss_download_data():
    """Download the latest bangumi-data and rebuild the Mikan mapping."""
    script = Path(__file__).parent.parent / "scripts" / "download_bangumi_data.py"
    if not script.exists():
        raise HTTPException(500, f"下载脚本不存在: {script}")

    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "下载超时，请重试")

    if proc.returncode != 0:
        raise HTTPException(500, f"下载失败:\n{proc.stderr or proc.stdout}")

    # Clear the in-memory cache so it reloads
    from . import data as data_module
    data_module._bangumi_mikan_map = None

    return {"ok": True, "output": proc.stdout}


# ── /api/rss/subscriptions ──

@app.get("/api/rss/subscriptions", response_model=list[SubscriptionOut])
async def list_subscriptions():
    """List all saved RSS subscriptions (with downloaded episode counts)."""
    subs = data.list_subscriptions()
    for s in subs:
        eps = data.get_all_episodes(s["bangumi_id"])
        s["downloaded_count"] = len(eps)
    return subs


@app.post("/api/rss/subscriptions", response_model=SubscriptionOut, status_code=201)
async def create_subscription(body: SubscriptionIn):
    """Add or update a subscription.  The body is the complete desired state."""
    sub = data.add_subscription(
        name=body.name,
        rss_url=body.rss_url,
        bangumi_id=body.bangumi_id,
        subgroup_id=body.subgroup_id,
        subgroup_name=body.subgroup_name,
        filter_tags=body.filter_tags,
        backup_rss_url=body.backup_rss_url,
        backup_subgroup_id=body.backup_subgroup_id,
        backup_subgroup_name=body.backup_subgroup_name,
        backup_filter_tags=body.backup_filter_tags,
        download_path=body.download_path,
    )
    # Enrich with Bangumi chain metadata — skip if any existing
    # subscription for this bangumi_id already has bgm_season cached.
    need_enrich = "bgm_season" not in sub
    if not need_enrich:
        # Check sibling subscriptions for cached fields
        all_subs = data.list_subscriptions()
        for s in all_subs:
            if s["bangumi_id"] == body.bangumi_id and "bgm_season" in s:
                sub.update({k: s[k] for k in ("bgm_season", "bgm_sortrange", "tmdb_id", "tmdb_season") if k in s})
                data.update_subscription(body.bangumi_id, {k: s[k] for k in ("bgm_season", "bgm_sortrange", "tmdb_id", "tmdb_season") if k in s})
                need_enrich = False
                break
    if need_enrich:
        try:
            enriched = await downloader.enrich_subscription(body.bangumi_id)
            if enriched:
                data.update_subscription(body.bangumi_id, enriched)
                sub.update(enriched)
        except Exception:
            pass  # Non-fatal: downloader will lazy-enrich on first poll

    # Fetch Bangumi poster CDN URL (non-fatal: falls back to gradient placeholder)
    try:
        poster_url = await image_service.get_subscription_poster_url(body.bangumi_id)
        if poster_url:
            data.update_subscription(body.bangumi_id, {"poster_url": poster_url})
            sub["poster_url"] = poster_url
    except Exception:
        pass  # Non-fatal: frontend falls back to gradient placeholder

    return sub


@app.delete("/api/rss/subscriptions/{bangumi_id}")
async def delete_subscription(bangumi_id: int):
    """Remove an RSS subscription by Bangumi ID."""
    if data.remove_subscription(bangumi_id):
        return {"ok": True}
    raise HTTPException(404, "订阅不存在")


@app.get("/api/rss/feed", response_model=RssFeedResponse)
async def get_rss_feed(
    url: str,
    subscription_id: str | None = None,
    tags: str | None = None,
):
    """Fetch and parse a Mikan RSS feed.

    If *subscription_id* is provided, uses that sub's filter tags.
    Otherwise *tags* can be passed directly (comma-separated) for preview.
    """
    filter_tags: list[str] | None = None
    if subscription_id:
        subs = data.list_subscriptions()
        for s in subs:
            if s["bangumi_id"] == int(subscription_id):
                filter_tags = s.get("filter_tags", [])
                break
    elif tags:
        filter_tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not filter_tags:
        filter_tags = None
    try:
        return await rss_service.fetch_and_parse_rss(url, filter_tags)
    except Exception as e:
        raise HTTPException(502, f"RSS 获取失败: {e}")


# ── /api/rss/downloader ──

@app.get("/api/rss/downloader/status")
async def downloader_status():
    return downloader.get_status()


@app.post("/api/rss/downloader/start")
async def downloader_start():
    await downloader.start()
    return {"ok": True}


@app.post("/api/rss/downloader/stop")
async def downloader_stop():
    await downloader.stop()
    return {"ok": True}


@app.post("/api/rss/downloader/run-once")
async def downloader_run_once():
    await downloader.run_once()
    return {"ok": True}


@app.get("/api/rss/downloader/config")
async def downloader_config():
    return downloader.get_config()


class IntervalBody(BaseModel):
    minutes: int

@app.patch("/api/rss/downloader/config")
async def downloader_set_interval(body: IntervalBody):
    return await downloader.set_interval(body.minutes)


@app.get("/api/rss/downloader/qbit-check")
async def downloader_qbit_check():
    return await downloader.check_qbit()


# ── /api/rss/subscriptions/{bangumi_id}/history ──

@app.get("/api/rss/subscriptions/{bangumi_id}/history")
async def subscription_history(bangumi_id: int):
    """Return download history for a subscription, enriched with qBittorrent status."""

    # 1. Subscription info
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    name = sub["name"] if sub else str(bangumi_id)
    bgm_season = sub.get("bgm_season", 1) if sub else 1
    bgm_sortrange = sub.get("bgm_sortrange", [0, 0]) if sub else [0, 0]

    # 2. Download history
    episodes_raw = data.get_all_episodes(bangumi_id)
    hashes = []
    entries = []
    for sort_str, ep in episodes_raw.items():
        h = ep.get("info_hash", "")
        entries.append({
            "sort": int(sort_str),
            "source": ep.get("source", ""),
            "guid": ep.get("guid", ""),
            "at": ep.get("at", ""),
            "info_hash": h,
        })
        if h:
            hashes.append(h)

    # 3. Query qBittorrent
    qbit_info = {}
    if hashes:
        try:
            qb = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
            qbit_info = await get_torrents_by_hashes(qb, hashes)
        except Exception:
            pass

    # 4. Merge
    for e in entries:
        h = e["info_hash"]
        e["qbit"] = qbit_info.get(h) if h else None

    # 5. Missing sorts in range
    downloaded_sorts = {e["sort"] for e in entries}
    missing = []
    if bgm_sortrange[0] > 0:
        for s in range(bgm_sortrange[0], bgm_sortrange[1] + 1):
            if s not in downloaded_sorts:
                missing.append(s)

    return {
        "bangumi_id": bangumi_id,
        "name": name,
        "bgm_season": bgm_season,
        "bgm_sortrange": bgm_sortrange,
        "episodes": entries,
        "missing_sorts": missing,
    }


@app.patch("/api/rss/subscriptions/{bangumi_id}/activate")
async def activate_subscription(bangumi_id: int):
    """Re-activate a completed subscription (set active=1)."""
    ok = data.update_subscription(bangumi_id, {"active": 1})
    if not ok:
        raise HTTPException(404, "订阅不存在")
    return {"ok": True}


# ── /api/rss/settings ──

@app.get("/api/rss/settings")
async def get_rss_settings():
    """Get global RSS settings (exclude patterns, etc.)."""
    return data.get_rss_settings()


@app.put("/api/rss/settings")
async def update_rss_settings(changes: dict[str, object]):
    """Update global RSS settings."""
    return data.update_rss_settings(changes)


# ═══════════════════════════════════════════════════════════════════════
# SPA fallback — MUST be the LAST route registered
# ═══════════════════════════════════════════════════════════════════════

@app.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend(full_path: str):
    """Serve the React SPA for all non-API routes (production mode).

    Registered LAST so that all explicit routes (/config, /api/*, /scan,
    /watch) take priority.  Only fires for frontend navigation
    paths that don't match any other route.
    """
    if not _frontend_dist.exists():
        raise HTTPException(404, "Frontend not built (run: cd frontend && npm run build)")

    # Try to serve the exact file
    file_path = _frontend_dist / full_path
    if file_path.is_file():
        from fastapi.responses import FileResponse
        return FileResponse(str(file_path))

    # SPA fallback: serve index.html for client-side routing
    index_path = _frontend_dist / "index.html"
    if index_path.exists():
        from fastapi.responses import FileResponse
        return FileResponse(str(index_path))

    raise HTTPException(404, "Not found")
