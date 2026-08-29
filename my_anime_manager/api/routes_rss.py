"""API routes: /api/rss/* — subscriptions, enrichment, feed, history."""

import asyncio
import json as _json
import logging
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import config, data
from ..clients import bangumi as bgm_client
from ..clients import mikan as mikan_client
from ..clients.qbittorrent import (
    delete_torrent,
    get_torrents_by_hashes,
    login as qb_login,
)
from ..services import downloader
from ..services import rss as rss_service
from ..services import tmdb as tmdb_service
from ..services.enrich import _compute_rss_offset
from ..services.nfo import images as image_service
from .models import (
    AssignMikanRequest,
    BangumiRssResponse,
    ManualSubscribeIn,
    MikanSearchResult,
    RssFeedResponse,
    SeasonInfo,
    SetTmdbRequest,
    SubscriptionIn,
    SubscriptionOut,
    TmdbEpisodeInfo,
    TmdbSearchResult,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# ── /api/rss/bangumi/{id} ──

@router.get("/api/rss/search")
async def search_bangumi(q: str):
    """Search bangumi_mikan_map by name. Returns up to 20 matches."""
    return data.search_by_name(q)


@router.get("/api/rss/mikan-search")
async def search_mikan(q: str = ""):
    """Search Mikan by name and return matching anime entries.

    Proxies to Mikan's own search page and parses the HTML.
    Returns a list of {mikan_id, title, url} dicts.
    """
    if not q.strip():
        return []
    try:
        results = await mikan_client.search_mikan(q.strip())
        return [MikanSearchResult(**r) for r in results]
    except Exception as e:
        raise HTTPException(502, f"Mikan 搜索失败: {e}")


@router.get("/api/rss/bangumi/{bangumi_id}/meta")
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


@router.get("/api/rss/bangumi/{bangumi_id}", response_model=BangumiRssResponse)
async def get_bangumi_rss(bangumi_id: int):
    """Look up Mikan subtitle groups and their RSS URLs for a Bangumi subject ID.

    Maps Bangumi subject ID → Mikan ID via bangumi-data, then scrapes the
    Mikan page to extract all subtitle groups and their RSS feed URLs.
    """
    result = await rss_service.lookup_bangumi_rss(bangumi_id)
    if result is None:
        raise HTTPException(404, f"未找到 Bangumi ID {bangumi_id} 对应的 Mikan 条目")
    return BangumiRssResponse(**result)


@router.post("/api/rss/bangumi/{bangumi_id}/assign-mikan", response_model=BangumiRssResponse)
async def assign_mikan_id(bangumi_id: int, body: AssignMikanRequest):
    """Assign a Mikan ID to a Bangumi entry and return subtitle groups.

    Saves the mapping to bangumi_mikan_map.json so future lookups work.
    Then fetches subtitle groups from Mikan for the given mikan_id.
    """
    name = data.get_bangumi_name(bangumi_id)
    if not name:
        raise HTTPException(404, f"Bangumi ID {bangumi_id} 不存在于映射表中")

    if not data.set_mikan_id(bangumi_id, body.mikan_id):
        raise HTTPException(404, f"Bangumi ID {bangumi_id} 不存在于映射表中")

    result = await rss_service.lookup_mikan_rss(body.mikan_id, bangumi_id, name)
    if not result["groups"]:
        raise HTTPException(404, "该 Mikan ID 对应的条目为空")
    return BangumiRssResponse(**result)


@router.get("/api/rss/data-status")
async def rss_data_status():
    """Check whether the bangumi-data mapping file exists."""
    from ..data import _MAP_FILE
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


@router.post("/api/rss/download-data")
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
    from .. import data as data_module
    data_module._bangumi_mikan_map = None

    return {"ok": True, "output": proc.stdout}


# ── /api/rss/subscriptions ──

@router.get("/api/rss/subscriptions", response_model=list[SubscriptionOut])
async def list_subscriptions():
    """List all saved RSS subscriptions (with downloaded episode counts)."""
    subs = data.list_subscriptions()
    for s in subs:
        eps = data.get_all_episodes(s["bangumi_id"])
        s["downloaded_count"] = len(eps)
    return subs


@router.post("/api/rss/subscriptions", response_model=SubscriptionOut, status_code=201)
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
        if s["bangumi_id"] == body.bangumi_id and "bgm" in s:
            cached = {g: s[g] for g in ENRICH_GROUPS if g in s}
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


@router.post("/api/rss/manual-subscribe", response_model=SubscriptionOut, status_code=201)
async def manual_subscribe(body: ManualSubscribeIn):
    """Create a subscription with manually provided RSS URLs.

    Used when Mikan search returns no results and the user enters RSS
    URLs directly.  No subtitle group is associated (subgroup_id = 0).
    """
    sub = data.add_subscription(
        name=body.name,
        rss_url=body.rss_url,
        bangumi_id=body.bangumi_id,
        subgroup_id=0,
        subgroup_name="手动",
        backup_rss_url=body.backup_rss_url or "",
    )
    eps = data.get_all_episodes(sub["bangumi_id"])
    sub["downloaded_count"] = len(eps)
    return SubscriptionOut(**sub)


ENRICH_GROUPS = ("bgm", "tvdb", "tmdb", "series_name")


def _get_cached_enrichment(bangumi_id: int) -> dict | None:
    """Return cached enrichment fields if this bangumi_id already has them.

    When a subscription already has enrichment data (e.g. from a sibling
    primary/backup subscription), we can skip the full Bangumi API chain.

    Only returns cached data that looks valid — a failed enrichment
    (sortrange [0,0] with no IDs) is treated as no cache so it can be
    retried on the next attempt.
    """
    subs = data.list_subscriptions()
    for s in subs:
        if s.get("bangumi_id") != bangumi_id:
            continue
        bgm = s.get("bgm")
        if not bgm:
            continue
        tvdb = s.get("tvdb", {})
        tmdb = s.get("tmdb", {})
        # A valid enrichment has at least one of: episode range, TVDB ID, or TMDB ID
        has_eps = (bgm.get("sortrange") or [0, 0])[1] > 0
        has_tvdb = (tvdb.get("id") or 0) > 0
        has_tmdb = (tmdb.get("id") or 0) > 0
        if has_eps or has_tvdb or has_tmdb:
            return {g: s[g] for g in ENRICH_GROUPS if g in s}
        # Stale/failed enrichment — ignore and re-run
        return None
    return None


@router.post("/api/rss/subscriptions/{bangumi_id}/enrich-stream")
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
        # enrichment data from a sibling subscription, skip the expensive
        # Bangumi API chain.  We still compute RSS offsets because they
        # depend on the RSS feed contents, not on Bangumi metadata, and may
        # have been wiped (e.g. when adding a second feed via add_subscription).
        cached = _get_cached_enrichment(bangumi_id)
        if cached:
            yield (_json.dumps({"type": "step", "message": "Using cached enrichment"}, ensure_ascii=False) + "\n").encode("utf-8")

            # Re-compute RSS offsets from cached bgm data + current RSS URLs
            subs = data.list_subscriptions()
            sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
            primary_rss = sub.get("primary", {}).get("rss_url", "") if sub else ""
            backup_rss = sub.get("backup", {}).get("rss_url", "") if sub else ""

            primary_offset: int | None = None
            backup_offset: int | None = None
            bgm_sortrange = cached.get("bgm", {}).get("sortrange")
            air_date = cached.get("bgm", {}).get("air_date", "")
            if bgm_sortrange and bgm_sortrange[0] > 0 and air_date:
                first_sort = bgm_sortrange[0]
                if primary_rss:
                    smallest = await _compute_rss_offset(primary_rss, air_date)
                    if smallest is not None:
                        primary_offset = first_sort - smallest
                if backup_rss:
                    smallest = await _compute_rss_offset(backup_rss, air_date)
                    if smallest is not None:
                        backup_offset = first_sort - smallest

            if primary_offset is not None:
                data.set_subscription_rss_offset(bangumi_id, "primary", primary_offset)
            if backup_offset is not None:
                data.set_subscription_rss_offset(bangumi_id, "backup", backup_offset)

            cached["primary_offset"] = primary_offset
            cached["backup_offset"] = backup_offset
            yield (_json.dumps({"type": "done", "result": cached}, ensure_ascii=False) + "\n").encode("utf-8")
            return

        queue: asyncio.Queue = asyncio.Queue()

        def on_progress(msg: str):
            queue.put_nowait({"type": "step", "message": msg})

        async def run():
            try:
                # Look up subscription to get RSS URLs for offset computation
                subs = data.list_subscriptions()
                sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
                primary_rss = sub.get("primary", {}).get("rss_url", "") if sub else ""
                backup_rss = sub.get("backup", {}).get("rss_url", "") if sub else ""

                result = await downloader.enrich_subscription(
                    bangumi_id, on_progress=on_progress,
                    primary_rss_url=primary_rss,
                    backup_rss_url=backup_rss,
                )
                if result:
                    # Pop offsets before top-level update_subscription
                    primary_offset = result.pop("primary_offset", None)
                    backup_offset = result.pop("backup_offset", None)
                    data.update_subscription(bangumi_id, result)
                    # Write offsets into nested primary/backup keys
                    if primary_offset is not None:
                        data.set_subscription_rss_offset(bangumi_id, "primary", primary_offset)
                    if backup_offset is not None:
                        data.set_subscription_rss_offset(bangumi_id, "backup", backup_offset)
                    # Restore for the stream response
                    result["primary_offset"] = primary_offset
                    result["backup_offset"] = backup_offset
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


@router.get("/api/rss/tmdb-search")
async def search_tmdb_shows(q: str) -> list[TmdbSearchResult]:
    """Search TMDB for TV shows (for manual TMDB ID assignment).

    Used by the frontend Tier-2 manual fallback when a subscription's
    TMDB ID could not be auto-inferred during enrichment.
    """
    from .clients import tmdb as tmdb_client
    try:
        res = await tmdb_client.search_tv(q, language="zh-CN")
        data_json = res.json()
    except Exception as e:
        raise HTTPException(502, f"TMDB search failed: {e}")

    results: list[TmdbSearchResult] = []
    for r in data_json.get("results", [])[:10]:
        results.append(TmdbSearchResult(
            id=r["id"],
            name=r.get("name", ""),
            original_name=r.get("original_name", ""),
            first_air_date=r.get("first_air_date", ""),
            poster_path=r.get("poster_path", ""),
        ))
    return results


@router.patch("/api/rss/subscriptions/{bangumi_id}/tmdb")
async def set_subscription_tmdb(bangumi_id: int, body: SetTmdbRequest):
    """Manually set the TMDB ID (and optional season) for a subscription.

    Persists to both subscriptions.json and bangumi_mikan_map.json.
    Used by the Tier-2 manual override in the frontend.
    """
    # Update the subscription record
    fields: dict = {"tmdb": {"id": body.tmdb_id}}
    if body.tmdb_season is not None:
        fields["tmdb"]["season"] = body.tmdb_season
    ok = data.update_subscription(bangumi_id, fields)
    if not ok:
        raise HTTPException(404, f"Subscription not found: {bangumi_id}")

    # Also persist to bangumi_mikan_map so future auto-lookups work
    data.set_tmdb_id(bangumi_id, body.tmdb_id, body.tmdb_season)

    logger.info(
        "manual tmdb override: bangumi=%d → tmdb_id=%d season=%s",
        bangumi_id, body.tmdb_id, body.tmdb_season,
    )
    return {"ok": True}


@router.delete("/api/rss/subscriptions/{bangumi_id}")
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
                logger.warning("qBittorrent 连接失败，跳过种子删除: %s", e)
        data.clear_download_history(bangumi_id)

    if data.remove_subscription(bangumi_id):
        return {"ok": True}
    raise HTTPException(404, "订阅不存在")


@router.get("/api/rss/feed", response_model=RssFeedResponse)
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
                filter_tags = s.get("primary", {}).get("filter_tags", [])
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




# ── /api/rss/subscriptions/{bangumi_id}/history ──

@router.get("/api/rss/subscriptions/{bangumi_id}/history")
async def subscription_history(bangumi_id: int):
    """Return download history for a subscription, enriched with qBittorrent status."""

    # 1. Subscription info
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    name = sub["name"] if sub else str(bangumi_id)
    bgm_season = sub.get("bgm", {}).get("season", 1) if sub else 1
    bgm_sortrange = sub.get("bgm", {}).get("sortrange", [0, 0]) if sub else [0, 0]

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


@router.get("/api/rss/tmdb/{tmdb_id}/seasons")
async def get_tmdb_seasons(tmdb_id: int) -> dict:
    """Fetch all TMDB seasons and episodes for a TV show.

    Calls build_season_episode_map to get every season's episode list,
    then converts to SeasonInfo / TmdbEpisodeInfo Pydantic models.
    Includes a ``_show_name`` sentinel key so the frontend can display
    the show title (e.g. "xxx (83121)") without a second round-trip.
    """
    season_map = await tmdb_service.build_season_episode_map(tmdb_id)
    result: dict = {}
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

    # Attach show name so the frontend can display "中文名 (ID)"
    try:
        from .clients import tmdb as _tmdb
        _detail_res = await _tmdb.get_tv_detail(tmdb_id)
        _detail = _detail_res.json()
        result["_show_name"] = _detail.get("name", str(tmdb_id))
    except Exception:
        result["_show_name"] = str(tmdb_id)

    return result


@router.get("/api/rss/subscriptions/{bangumi_id}/history-stream")
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
        bgm_season = sub.get("bgm", {}).get("season", 1) if sub else 1
        bgm_sortrange = sub.get("bgm", {}).get("sortrange", [0, 0]) if sub else [0, 0]

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


@router.patch("/api/rss/subscriptions/{bangumi_id}/activate")
async def activate_subscription(bangumi_id: int):
    """Re-activate a completed subscription (set active=1)."""
    ok = data.update_subscription(bangumi_id, {"active": 1})
    if not ok:
        raise HTTPException(404, "订阅不存在")
    return {"ok": True}


@router.patch("/api/rss/subscriptions/{bangumi_id}")
async def update_subscription_fields(bangumi_id: int, fields: dict[str, object]):
    """Update specific fields of a subscription (e.g. exclude_patterns)."""
    ok = data.update_subscription(bangumi_id, fields)
    if not ok:
        raise HTTPException(404, "订阅不存在")
    return {"ok": True}


@router.delete("/api/rss/subscriptions/{bangumi_id}/rss")
async def delete_subscription_rss(bangumi_id: int, type: str = "primary"):
    """Clear primary or backup RSS from a subscription.

    If no RSS remains after clearing, the entire subscription is deleted.
    """
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if not sub:
        raise HTTPException(404, "订阅不存在")

    if type == "primary":
        fields = {"primary": {"rss_url": "", "subgroup_id": 0, "subgroup_name": "",
                              "filter_tags": [], "exclude_patterns": []}}
    else:
        fields = {"backup": {"rss_url": "", "subgroup_id": 0,
                             "subgroup_name": "", "filter_tags": [],
                             "exclude_patterns": []}}

    data.update_subscription(bangumi_id, fields)

    # Reload and check if any RSS remains
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if sub and not sub.get("primary", {}).get("rss_url") and not sub.get("backup", {}).get("rss_url"):
        data.remove_subscription(bangumi_id)
        return {"ok": True, "deleted": True}

    return {"ok": True, "deleted": False}


