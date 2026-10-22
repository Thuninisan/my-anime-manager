"""API routes: /api/rss/* — subscriptions, enrichment, feed, history."""

import asyncio
import json as _json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from fastapi.responses import FileResponse, Response, StreamingResponse

from .. import config, data
from ..clients import bangumi as bgm_client
from ..clients import mikan as mikan_client
from ..clients.qbittorrent import (
    delete_torrent,
    get_torrents_by_hashes,
    login as qb_login,
)
from ..services.mappings import sync as mapping_sync
from ..services.subscriptions import enrichment
from ..services.discovery import search as discovery_search
from ..services.mappings import bindings
from ..services.subscriptions import initial_poll as initial_rss_poll
from ..services.subscriptions import feeds as rss_service
from ..services.metadata import posters as rss_poster
from ..services.metadata.providers import tmdb as tmdb_service
from .models import (
    AssignMikanRequest,
    BangumiRssResponse,
    ManualSubscribeIn,
    MappingPatch,
    MappingChangeConfirm,
    MikanSearchResult,
    RssFeedResponse,
    SeasonInfo,
    SetTmdbRequest,
    SubscriptionIn,
    SubscriptionOut,
    TmdbEpisodeInfo,
    TmdbSearchResult,
)

from .external_api_v1_adapter import V1SubscriptionOut
from ..db.mapping_changes import MappingConflict
from ..services.mappings import changes as mapping_changes

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/api/rss/bangumi/{bangumi_id}/mapping")
async def get_bangumi_mapping(bangumi_id: int):
    from ..db.mapping_fields import MAPPING_FIELDS
    mapping = data.get_mapping(bangumi_id)
    if not mapping:
        raise HTTPException(404, "映射不存在")
    return {"bangumi_id": bangumi_id, **{field: mapping.get(field) for field in MAPPING_FIELDS},
            "override": mapping.get("override", False)}


@router.patch("/api/rss/bangumi/{bangumi_id}/mapping")
async def patch_bangumi_mapping(bangumi_id: int, body: MappingPatch):
    try:
        changes = body.model_dump(exclude_unset=True)
        current = data.get_mapping(bangumi_id)
        if ("mikan_id" in changes and changes["mikan_id"] != current.get("mikan_id")
                and any(sub["bangumi_id"] == bangumi_id for sub in data.list_subscriptions())):
            raise MappingConflict("已有订阅，修改 Mikan ID 前须确认旧订阅和文件清理")
        if not data.edit_mapping(bangumi_id, changes):
            raise HTTPException(404, "映射不存在")
    except MappingConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return await get_bangumi_mapping(bangumi_id)


@router.post("/api/rss/bangumi/{bangumi_id}/mapping/preview")
async def preview_mapping_change(bangumi_id: int, body: MappingPatch):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "没有需要保存的修改")
    try:
        return await mapping_changes.preview(bangumi_id, changes)
    except MappingConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.exception("映射影响预览失败：bangumi=%s", bangumi_id)
        raise HTTPException(502, f"无法读取旧订阅的文件清单：{exc}") from exc


@router.post("/api/rss/bangumi/{bangumi_id}/mapping/confirm", status_code=202)
async def confirm_mapping_change(bangumi_id: int, body: MappingChangeConfirm):
    try:
        return mapping_changes.confirm(bangumi_id, body.task_id)
    except MappingConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/rss/bangumi/{bangumi_id}/mapping/regen-nfo", status_code=202)
async def regenerate_mapping_nfo(bangumi_id: int, body: MappingChangeConfirm):
    try:
        return mapping_changes.start_nfo(bangumi_id, body.task_id)
    except MappingConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/rss/bangumi/{bangumi_id}/mapping/tasks/{task_id}/retry", status_code=202)
async def retry_mapping_task(bangumi_id: int, task_id: str):
    try:
        return mapping_changes.retry(bangumi_id, task_id)
    except MappingConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/rss/bangumi/{bangumi_id}/mapping/tasks/{task_id}")
async def mapping_task_status(bangumi_id: int, task_id: str):
    task = mapping_changes.get_task(bangumi_id, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return task


@router.get("/api/rss/bangumi/{bangumi_id}/mapping/tasks")
async def latest_mapping_task(bangumi_id: int):
    return mapping_changes.latest_task(bangumi_id)


@router.get("/api/rss/bangumi/{bangumi_id}/poster")
async def get_bangumi_poster(bangumi_id: int):
    if bangumi_id <= 0:
        raise HTTPException(404, "封面不存在")
    path = await rss_poster.get_poster(bangumi_id)
    if path is None:
        raise HTTPException(404, "封面不存在")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/rss/bangumi-search/{bangumi_id}/poster")
async def get_bangumi_search_poster(bangumi_id: int):
    poster = await rss_poster.get_search_poster(bangumi_id)
    if poster is None:
        raise HTTPException(404, "封面不存在")
    image, content_type = poster
    return Response(image, media_type=content_type, headers={"Cache-Control": "private, max-age=600"})


# ── /api/rss/bangumi/{id} ──

@router.get("/api/rss/search")
async def search_bangumi(q: str):
    """Search community mapping by name. Returns up to 20 matches."""
    return data.search_by_name(q)


@router.get("/api/rss/bangumi-search")
async def search_bangumi_online(q: str = ""):
    try:
        return await discovery_search.search_bangumi(q)
    except discovery_search.SearchUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc


class BangumiSelection(BaseModel):
    bangumi_id: int
    name: str
    name_original: str = ""


@router.post("/api/rss/bangumi-selection")
async def save_bangumi_selection(body: BangumiSelection):
    try:
        return bindings.select_bangumi(body.bangumi_id, body.name, body.name_original)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


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
        poster_url = rss_poster.poster_url(bangumi_id) if images else ""
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

    Saves the mapping in SQLite so future lookups work.
    Then fetches subtitle groups from Mikan for the given mikan_id.
    """
    from ..db.mapping_changes import ensure_idle
    ensure_idle(bangumi_id)
    if (data.get_mikan_id(bangumi_id) != body.mikan_id
            and any(sub["bangumi_id"] == bangumi_id for sub in data.list_subscriptions())):
        raise HTTPException(409, "已有订阅，请通过编辑映射确认清理旧订阅后更换 Mikan ID")
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
    """Report the active database mapping."""
    return await asyncio.to_thread(mapping_sync.status)


@router.post("/api/rss/download-data")
async def rss_download_data():
    """Update community mappings directly in SQLite."""
    try:
        return await mapping_sync.synchronize()
    except mapping_sync.MappingSyncBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, "映射更新超时，原有数据已保留，请重试") from exc
    except Exception as exc:
        raise HTTPException(502, "映射更新失败，原有数据已保留，请检查网络后重试") from exc


# ── /api/rss/subscriptions ──

@router.get("/api/rss/subscriptions", response_model=list[V1SubscriptionOut], deprecated=True)
@router.get("/api/rss/v2/subscriptions", response_model=list[SubscriptionOut])
async def list_subscriptions():
    """List all saved RSS subscriptions (with downloaded episode counts)."""
    subs = data.list_subscriptions()
    for s in subs:
        eps = data.get_all_episodes(s["bangumi_id"])
        s["downloaded_count"] = len(eps)
        for source in ("primary", "backup"):
            s[f"{source}_max_episode"] = max(
                (int(sort) for sort, episode in eps.items()
                 if episode.get("source") == source and str(sort).isdigit()),
                default=0,
            )
        s["poster_url"] = rss_poster.poster_url(s["bangumi_id"])
    return subs


@router.post("/api/rss/subscriptions", response_model=V1SubscriptionOut, status_code=201, deprecated=True)
@router.post("/api/rss/v2/subscriptions", response_model=SubscriptionOut, status_code=201)
async def create_subscription(body: SubscriptionIn):
    """Add or update a subscription.  The body is the complete desired state."""
    is_new = not any(s["bangumi_id"] == body.bangumi_id for s in data.list_subscriptions())
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
    # New subscriptions use the server-owned first poll; existing subscriptions
    # can refresh via enrich-stream. Return immediately, retaining saved fields.
    all_subs = data.list_subscriptions()
    for s in all_subs:
        if s["bangumi_id"] == body.bangumi_id and "bgm" in s:
            cached = enrichment.copy_enrichment_fields(s)
            sub.update(cached)
            break

    sub["poster_url"] = rss_poster.poster_url(body.bangumi_id)

    if is_new:
        initial_rss_poll.start(body.bangumi_id)

    return sub


@router.post("/api/rss/manual-subscribe", response_model=V1SubscriptionOut, status_code=201, deprecated=True)
@router.post("/api/rss/v2/manual-subscribe", response_model=SubscriptionOut, status_code=201)
async def manual_subscribe(body: ManualSubscribeIn):
    """Create a subscription with manually provided RSS URLs.

    Used when Mikan search returns no results and the user enters RSS
    URLs directly.  No subtitle group is associated (subgroup_id = 0).
    """
    is_new = not any(s["bangumi_id"] == body.bangumi_id for s in data.list_subscriptions())
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
    sub["poster_url"] = rss_poster.poster_url(body.bangumi_id)
    if is_new:
        initial_rss_poll.start(body.bangumi_id)
    return SubscriptionOut(**sub)


@router.get("/api/rss/subscriptions/{bangumi_id}/initial-poll")
async def initial_poll_status(bangumi_id: int):
    """Return progress of the server-owned first poll for a new subscription."""
    status = initial_rss_poll.get_status(bangumi_id)
    if status is None:
        raise HTTPException(404, "没有首次轮询任务")
    return status


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
        # Keep cache refresh request-owned and emit its step before fetching feeds.
        cached = enrichment.get_cached_enrichment(bangumi_id)
        if cached:
            yield (_json.dumps({"type": "step", "message": "Using cached enrichment"}, ensure_ascii=False) + "\n").encode("utf-8")
            result = await enrichment.refresh_cached_enrichment(bangumi_id, cached)
            yield (_json.dumps({"type": "done", "result": result}, ensure_ascii=False) + "\n").encode("utf-8")
            return

        queue: asyncio.Queue = asyncio.Queue()

        def on_progress(msg: str):
            queue.put_nowait({"type": "step", "message": msg})

        async def run():
            try:
                result = await enrichment.enrich_saved_subscription(
                    bangumi_id, on_progress=on_progress,
                )
                queue.put_nowait({"type": "done", "result": result})
            except Exception as exc:
                queue.put_nowait({"type": "error", "message": str(exc)})

        # Full enrichment remains independent of the streaming connection.
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
    from ..clients import tmdb as tmdb_client
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

    Persists the subscription and Bangumi mapping in SQLite.
    Used by the Tier-2 manual override in the frontend.
    """
    # Update the subscription record
    fields: dict = {"tmdb": {"id": body.tmdb_id}}
    if body.tmdb_season is not None:
        fields["tmdb"]["season"] = body.tmdb_season
    try:
        ok = data.update_subscription(bangumi_id, fields)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not ok:
        raise HTTPException(404, f"Subscription not found: {bangumi_id}")

    # update_subscription commits the confirmed identity and mapping in one transaction.

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
    from ..db.mapping_changes import ensure_idle
    ensure_idle(bangumi_id)
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
            "download_path": ep.get("download_path"),
            "mapping": ep.get("mapping"),
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


@router.get("/api/rss/tmdb/{tmdb_id}/seasons", deprecated=True)
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
        from ..clients import tmdb as _tmdb
        _detail_res = await _tmdb.get_tv_detail(tmdb_id)
        _detail = _detail_res.json()
        result["_show_name"] = _detail.get("name", str(tmdb_id))
    except Exception:
        result["_show_name"] = str(tmdb_id)

    return result


@router.get("/api/rss/v2/subscriptions/{bangumi_id}/history-stream")
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
                "download_path": ep.get("download_path"),
                "mapping": ep.get("mapping"),
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
    from ..db.mapping_fields import MAPPING_FIELDS
    if any(field.startswith("_") or field in {*MAPPING_FIELDS, "tmdb", "tvdb", "resource_identity"}
           for field in fields):
        raise HTTPException(422, "平台映射请通过 mapping 接口修改")
    try:
        ok = data.update_subscription(bangumi_id, fields)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
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


@router.get("/api/rss/subscriptions/{bangumi_id}/history-stream", deprecated=True)
async def subscription_history_stream_v1(bangumi_id: int):
    """Keep unknown external stream clients behind the explicit v1 boundary."""
    from .external_api_v1_adapter import history_stream_v1
    response = await subscription_history_stream(bangumi_id)
    return StreamingResponse(history_stream_v1(response.body_iterator),
                             media_type="application/x-ndjson", headers=dict(response.headers))
