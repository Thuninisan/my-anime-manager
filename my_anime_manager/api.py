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
import json as _json
import logging
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
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .services.batch_service import build_preview, execute_confirm, process_torrent
from .services import rss as rss_service
from .services import downloader
from .services import tmdb as tmdb_service
from .services import image_downloader as image_service
from .clients.qbittorrent import login as qb_login, get_torrents_by_hashes, delete_torrent, add_torrent, resume_torrent, get_torrent_files
from .utils.torrent_hash import compute_info_hash
from .utils.torrent_file_reader import read_torrent_file_list
import bencodepy
from .clients import bangumi as bgm_client
from . import data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

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
    exclude_patterns: list[str] = []
    backup_exclude_patterns: list[str] = []


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
    exclude_patterns: list[str] = []
    backup_exclude_patterns: list[str] = []
    created_at: str = ""
    updated_at: str = ""
    download_path: str = ""
    active: int = 1
    # Pre-computed season metadata (from Bangumi chain)
    bgm_season: int = 1
    bgm_sortrange: list[int] = []
    # Bangumi rating (from subject API)
    bgm_rating: float = 0.0
    bgm_rating_total: int = 0
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

@app.get("/api/rss/search")
async def search_bangumi(q: str):
    """Search bangumi_mikan_map by name. Returns up to 20 matches."""
    return data.search_by_name(q)


@app.get("/api/rss/bangumi/{bangumi_id}/meta")
async def get_bangumi_meta(bangumi_id: int):
    """Fetch Bangumi subject metadata (air_date, eps, rating, series_name).
    Independent from the main RSS lookup — called in parallel by the frontend.
    """
    try:
        subject = await bgm_client.get_subject(bangumi_id)
        series_name = subject.get("name_cn") or subject.get("name", "")
        images = subject.get("images") or {}
        poster_url = (images.get("small") or images.get("grid") or images.get("medium") or "")
        return {
            "air_date": subject.get("date", "") or "",
            "eps": subject.get("eps") or subject.get("total_episodes") or 0,
            "rating": (subject.get("rating") or {}).get("score", 0) or 0,
            "rating_total": (subject.get("rating") or {}).get("total", 0) or 0,
            "series_name": series_name,
            "poster_url": poster_url,
        }
    except Exception as e:
        raise HTTPException(502, f"Bangumi API 失败: {e}")


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
        exclude_patterns=body.exclude_patterns,
        backup_exclude_patterns=body.backup_exclude_patterns,
    )
    # Enrichment is done asynchronously via the enrich-stream endpoint.
    # The subscription is returned immediately without enrichment data.
    # If a sibling subscription already has cached bgm_season, copy it.
    all_subs = data.list_subscriptions()
    for s in all_subs:
        if s["bangumi_id"] == body.bangumi_id and "bgm_season" in s:
            cached = {k: s[k] for k in ("bgm_season", "bgm_sortrange", "tmdb_id", "tmdb_season", "series_name", "bgm_rating", "bgm_rating_total") if k in s}
            data.update_subscription(body.bangumi_id, cached)
            sub.update(cached)
            break

    # Fetch Bangumi poster CDN URL (non-fatal: falls back to gradient placeholder)
    try:
        poster_url = await image_service.get_subscription_poster_url(body.bangumi_id)
        if poster_url:
            data.update_subscription(body.bangumi_id, {"poster_url": poster_url})
            sub["poster_url"] = poster_url
    except Exception:
        pass  # Non-fatal: frontend falls back to gradient placeholder

    return sub


ENRICH_FIELDS = ("bgm_season", "bgm_sortrange", "series_name", "tmdb_id", "tmdb_season", "bgm_rating", "bgm_rating_total")


def _get_cached_enrichment(bangumi_id: int) -> dict | None:
    """Return cached enrichment fields if this bangumi_id already has them.

    When a subscription already has enrichment data (e.g. from a sibling
    primary/backup subscription), we can skip the full Bangumi API chain.
    """
    subs = data.list_subscriptions()
    for s in subs:
        if s["bangumi_id"] == bangumi_id and "bgm_season" in s:
            return {k: s[k] for k in ENRICH_FIELDS if k in s}
    return None


@app.post("/api/rss/subscriptions/{bangumi_id}/enrich-stream")
async def enrich_subscription_stream(bangumi_id: int):
    """Stream enrichment progress as NDJSON (one JSON object per line).

    The client reads the response body line by line.  Each line is a
    JSON object with ``type``:

    - ``{"type": "step", "message": "✅ bgm_season=2"}`` — progress update
    - ``{"type": "done", "result": {...}}`` — enrichment succeeded
    - ``{"type": "error", "message": "..."}`` — enrichment failed
    """

    async def generate():
        # Check for cached enrichment — if this bangumi_id already has
        # enrichment data from a sibling subscription, return it immediately
        # instead of re-running the full Bangumi API chain.
        cached = _get_cached_enrichment(bangumi_id)
        if cached:
            yield (_json.dumps({"type": "step", "message": "Using cached enrichment"}, ensure_ascii=False) + "\n").encode("utf-8")
            yield (_json.dumps({"type": "done", "result": cached}, ensure_ascii=False) + "\n").encode("utf-8")
            return

        queue: asyncio.Queue = asyncio.Queue()

        def on_progress(msg: str):
            queue.put_nowait({"type": "step", "message": msg})

        async def run():
            try:
                result = await downloader.enrich_subscription(
                    bangumi_id, on_progress=on_progress,
                )
                if result:
                    data.update_subscription(bangumi_id, result)
                queue.put_nowait({"type": "done", "result": result})
            except Exception as exc:
                queue.put_nowait({"type": "error", "message": str(exc)})

        asyncio.create_task(run())

        while True:
            evt = await queue.get()
            line = _json.dumps(evt, ensure_ascii=False) + "\n"
            yield line.encode("utf-8")
            if evt["type"] in ("done", "error"):
                break

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/rss/subscriptions/{bangumi_id}")
async def delete_subscription(bangumi_id: int, delete_files: bool = False):
    """Remove an RSS subscription by Bangumi ID.

    If *delete_files* is True, also:
    - Delete all related torrents from qBittorrent (with files)
    - Clear download history for this bangumi_id
    """
    if delete_files:
        eps = data.get_all_episodes(bangumi_id)
        hashes = [e["info_hash"] for e in eps.values() if e.get("info_hash")]
        if hashes:
            try:
                qb = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
                for h in hashes:
                    try:
                        await delete_torrent(qb, str(h), delete_files=True)
                    except Exception:
                        pass  # best-effort per torrent
            except Exception as e:
                print(f"⚠️ qBittorrent 连接失败，跳过种子删除: {e}")
        data.clear_download_history(bangumi_id)

    if data.remove_subscription(bangumi_id):
        return {"ok": True}
    raise HTTPException(404, "订阅不存在")


@app.get("/api/rss/feed", response_model=RssFeedResponse)
async def get_rss_feed(
    url: str,
    subscription_id: str | None = None,
    tags: str | None = None,
    exclude_patterns: str = "",
):
    """Fetch and parse a Mikan RSS feed.

    If *subscription_id* is provided, uses that sub's filter tags.
    Otherwise *tags* can be passed directly (comma-separated) for preview.
    *exclude_patterns* is comma-separated and merged with global settings.
    """
    filter_tags: list[str] | None = None
    extra_exclude: list[str] | None = None
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
    if exclude_patterns:
        extra_exclude = [p.strip() for p in exclude_patterns.split(",") if p.strip()]
    try:
        return await rss_service.fetch_and_parse_rss(
            url, filter_tags, extra_exclude_patterns=extra_exclude,
        )
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


@app.get("/api/rss/tmdb/{tmdb_id}/seasons")
async def get_tmdb_seasons(tmdb_id: int) -> dict[str, SeasonInfo]:
    """Fetch all TMDB seasons and episodes for a TV show.

    Calls build_season_episode_map to get every season's episode list,
    then converts to SeasonInfo / TmdbEpisodeInfo Pydantic models.
    Returns an empty dict if TMDB has no data for this show.
    """
    season_map = await tmdb_service.build_season_episode_map(tmdb_id)
    result: dict[str, SeasonInfo] = {}
    for sk, sv in season_map.items():
        episodes = [
            TmdbEpisodeInfo(
                epNum=e["epNum"],
                name=e["name"],
                tmdbId=e["tmdbId"],
                overview=e.get("overview") or "",
                airDate=e.get("airDate") or "",
                runtime=e.get("runtime") or 0,
                stillPath=e.get("stillPath") or "",
            )
            for e in sv.get("episodes", [])
        ]
        result[str(sk)] = SeasonInfo(
            name=sv.get("name", f"Season {sk}"), episodes=episodes,
        )
    return result


@app.get("/api/rss/subscriptions/{bangumi_id}/history-stream")
async def subscription_history_stream(bangumi_id: int):
    """Stream download history + live qBittorrent updates as NDJSON.

    Events:
    - ``{"type": "data", ...}`` — full initial payload (subscription info,
      download history with qBittorrent status)
    - ``{"type": "update", "episodes": [...]}`` — periodic torrent status
      updates (only ``sort`` and ``qbit`` fields per episode)
    """

    async def generate():
        # ── Build initial data (same logic as /history) ──
        subs = data.list_subscriptions()
        sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
        name = sub["name"] if sub else str(bangumi_id)
        bgm_season = sub.get("bgm_season", 1) if sub else 1
        bgm_sortrange = sub.get("bgm_sortrange", [0, 0]) if sub else [0, 0]

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
                "tmdb_ep": ep.get("tmdb_ep"),
                "tmdb_season": ep.get("tmdb_season"),
            })
            if h:
                hashes.append(h)

        async def _fetch_qbit() -> dict[str, dict]:
            if not hashes:
                return {}
            try:
                qb = await qb_login(
                    config.QBITTORRENT_URL,
                    config.QBITTORRENT_USERNAME,
                    config.QBITTORRENT_PASSWORD,
                )
                return await get_torrents_by_hashes(qb, hashes)
            except Exception:
                return {}

        # Merge qBittorrent into entries
        def _merge_qbit(eps: list[dict], qbit: dict[str, dict]) -> None:
            for e in eps:
                h = e["info_hash"]
                e["qbit"] = qbit.get(h) if h else None

        qbit_info = await _fetch_qbit()
        _merge_qbit(entries, qbit_info)

        # Missing sorts
        downloaded_sorts = {e["sort"] for e in entries}
        missing = []
        if bgm_sortrange[0] > 0:
            for s in range(bgm_sortrange[0], bgm_sortrange[1] + 1):
                if s not in downloaded_sorts:
                    missing.append(s)

        # Send initial data frame
        line = _json.dumps({
            "type": "data",
            "bangumi_id": bangumi_id,
            "name": name,
            "bgm_season": bgm_season,
            "bgm_sortrange": bgm_sortrange,
            "episodes": entries,
            "missing_sorts": missing,
        }, ensure_ascii=False) + "\n"
        yield line.encode("utf-8")

        # ── Periodic qBittorrent updates ──
        try:
            while True:
                await asyncio.sleep(5)

                qbit_info = await _fetch_qbit()
                # Build slim update: only sort + qbit per episode
                updates = []
                for e in entries:
                    h = e["info_hash"]
                    new_qbit = qbit_info.get(h) if h else None
                    if new_qbit != e.get("qbit"):
                        e["qbit"] = new_qbit
                        updates.append({"sort": e["sort"], "qbit": new_qbit})

                if updates:
                    line = _json.dumps({
                        "type": "update",
                        "episodes": updates,
                    }, ensure_ascii=False) + "\n"
                    yield line.encode("utf-8")

        except asyncio.CancelledError:
            # Client disconnected — clean exit
            pass

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.patch("/api/rss/subscriptions/{bangumi_id}/activate")
async def activate_subscription(bangumi_id: int):
    """Re-activate a completed subscription (set active=1)."""
    ok = data.update_subscription(bangumi_id, {"active": 1})
    if not ok:
        raise HTTPException(404, "订阅不存在")
    return {"ok": True}


@app.patch("/api/rss/subscriptions/{bangumi_id}")
async def update_subscription_fields(bangumi_id: int, fields: dict[str, object]):
    """Update specific fields of a subscription (e.g. exclude_patterns)."""
    ok = data.update_subscription(bangumi_id, fields)
    if not ok:
        raise HTTPException(404, "订阅不存在")
    return {"ok": True}


@app.delete("/api/rss/subscriptions/{bangumi_id}/rss")
async def delete_subscription_rss(bangumi_id: int, type: str = "primary"):
    """Clear primary or backup RSS from a subscription.

    If no RSS remains after clearing, the entire subscription is deleted.
    """
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if not sub:
        raise HTTPException(404, "订阅不存在")

    if type == "primary":
        fields = {"rss_url": "", "subgroup_id": 0, "subgroup_name": "",
                   "filter_tags": [], "exclude_patterns": []}
    else:
        fields = {"backup_rss_url": "", "backup_subgroup_id": 0,
                   "backup_subgroup_name": "", "backup_filter_tags": [],
                   "backup_exclude_patterns": []}

    data.update_subscription(bangumi_id, fields)

    # Reload and check if any RSS remains
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if sub and not sub.get("rss_url") and not sub.get("backup_rss_url"):
        data.remove_subscription(bangumi_id)
        return {"ok": True, "deleted": True}

    return {"ok": True, "deleted": False}


# ── /api/rss/download-history/{bangumi_id}/{sort} ──

@app.delete("/api/rss/download-history/{bangumi_id}/{sort}")
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


@app.post("/api/rss/download-history/{bangumi_id}/{sort}")
async def add_episode_history(bangumi_id: int, sort: int):
    """Manually mark a missing episode as downloaded (source='manual')."""
    data.mark_downloaded(
        bangumi_id, sort,
        rss_url="", guid="", source="manual", pub_date="", info_hash="",
    )
    return {"ok": True}


@app.post("/api/rss/download-history/{bangumi_id}/{sort}/upload")
async def upload_episode_torrent(bangumi_id: int, sort: int, file: UploadFile = File(...)):
    """Upload a .torrent file to manually add a missing episode.

    1. Parse torrent → extract name + info_hash
    2. Determine save path from subscription (same logic as RSS downloader)
    3. Add to qBittorrent (paused)
    4. Record in download_history.json (source='add')
    """
    if not file.filename or not file.filename.lower().endswith(".torrent"):
        raise HTTPException(400, "Only .torrent files are accepted")

    # ── Read subscription ──
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    show_name = sub.get("name", str(bangumi_id))
    series_name = sub.get("series_name") or show_name
    bgm_season = sub.get("bgm_season", 1)
    tmdb_id = sub.get("tmdb_id", 0)
    tmdb_season = sub.get("tmdb_season")
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
    sub_path_template = sub.get("download_path") or "/{series_name}/Season {season}"
    sub_path = sub_path_template.format(
        series_name=series_name, show_name=show_name, season=bgm_season,
    ).strip("/")

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
                    base_path=rss_base,
                    sub_path=sub_path,
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


@app.post("/api/rss/download-history/{bangumi_id}/{sort}/replace")
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

    # ── Read subscription ──
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    show_name = sub.get("name", str(bangumi_id))
    series_name = sub.get("series_name") or show_name
    bgm_season = sub.get("bgm_season", 1)
    tmdb_id = sub.get("tmdb_id", 0)
    tmdb_season = sub.get("tmdb_season")
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
    sub_path_template = sub.get("download_path") or "/{series_name}/Season {season}"
    sub_path = sub_path_template.format(
        series_name=series_name, show_name=show_name, season=bgm_season,
    ).strip("/")

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
                    base_path=rss_base, sub_path=sub_path,
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


@app.patch("/api/rss/download-history/{bangumi_id}/{sort}")
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

    # ── Optional NFO regeneration ──
    if regen_nfo:
        subs = data.list_subscriptions()
        sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
        if sub:
            ep = data.get_all_episodes(bangumi_id).get(str(sort), {})
            info_hash = ep.get("info_hash", "")
            if info_hash and sub.get("tmdb_id"):
                try:
                    show_name = sub.get("name", str(bangumi_id))
                    series_name = sub.get("series_name") or show_name
                    bgm_season = sub.get("bgm_season", 1)
                    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
                    sub_path_template = sub.get("download_path") or "/{series_name}/Season {season}"
                    sub_path = sub_path_template.format(
                        series_name=series_name, show_name=show_name, season=bgm_season,
                    ).strip("/")
                    qb = await qb_login(
                        config.QBITTORRENT_URL,
                        config.QBITTORRENT_USERNAME,
                        config.QBITTORRENT_PASSWORD,
                    )
                    files = await get_torrent_files(qb, info_hash)
                    old_path = files[0]["name"] if files else ep.get("guid", "")
                    await downloader.generate_metadata(
                        qb, info_hash, bangumi_id, sort,
                        bangumi_id, sub["tmdb_id"], show_name,
                        old_path, ep.get("guid", ""),
                        bgm_season=bgm_season,
                        tmdb_season=sub.get("tmdb_season"),
                        base_path=rss_base, sub_path=sub_path,
                    )
                    logger.info("overrides+PATCH: NFO regenerated for bangumi=%d sort=%d", bangumi_id, sort)
                except Exception:
                    logger.exception("overrides+PATCH: NFO regeneration failed")

    return {"ok": True}

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
