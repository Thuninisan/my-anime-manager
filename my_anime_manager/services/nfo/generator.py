"""Episode file generation — thumbnail download + NFO writing.

Pure file-writing layer — no API calls.  Used by both the RSS flow
(:func:`~.metadata_builder.generate_metadata`) and the torrent batch
flow (:func:`~my_anime_manager.services.batch_service.generate_metadata_collection`).
"""

import logging
from pathlib import Path

from ... import config
from .images import download_episode_thumb
from .nfo_xml import generate_episode_nfo

logger = logging.getLogger(__name__)


def format_download_path(
    template: str, sub: dict, sort: int = 0, ext: str = "",
    bangumi_sort: int = 0, bangumi_ep: int = 0,
    tvdb_episode: int = 0, tmdb_episode: int = 0,
    tmdb_title: str = "",
) -> str:
    """Format a download path template with subscription and episode variables.

    Uses Python ``str.format()`` syntax.  Available variables:

    ==================== ============================================
    ``{series_name}``    Root series name (from Bangumi chain)
    ``{bangumi_title}``  Current season Bangumi entry name
    ``{bgm_season}``     Bangumi chain position (int)
    ``{tvdb_season}``    TVDB season number (int)
    ``{tmdb_season}``    TMDB season number (int)
    ``{bangumi_sort}``   Bangumi sort number (sequential within entry)
    ``{bangumi_ep}``     Bangumi canonical episode number
    ``{tvdb_episode}``   Matched TVDB episode number
    ``{tmdb_episode}``   Matched TMDB episode number
    ``{tmdb_title}``     TMDB show title (Chinese)
    ``{sort}``           Alias for ``{bangumi_sort}`` (deprecated)
    ==================== ============================================

    Format specs are supported, e.g. ``{tvdb_episode:02d}`` → ``05``.
    The *ext* parameter (e.g. ``".mkv"``) is appended after formatting.
    """
    bgm = sub.get("bgm", {})
    tvdb = sub.get("tvdb", {})
    tmdb = sub.get("tmdb", {})
    # Use ``is not None`` guards — season 0 is a valid value (Specials)
    # and must not be treated as falsy by ``or``.
    _tvdb_s = tvdb.get("season")
    _tmdb_s = tmdb.get("season")
    _bgm_s = bgm.get("season", 1)
    return template.format(
        series_name=sub.get("series_name") or sub.get("name", ""),
        bangumi_title=bgm.get("subject_name") or sub.get("name", ""),
        bgm_season=_bgm_s,
        tvdb_season=_tvdb_s if _tvdb_s is not None else _bgm_s,
        tmdb_season=_tmdb_s if _tmdb_s is not None else _bgm_s,
        sort=bangumi_sort or sort,
        bangumi_sort=bangumi_sort or sort,
        bangumi_ep=bangumi_ep or bangumi_sort or sort,
        tvdb_episode=tvdb_episode or bangumi_ep or bangumi_sort or sort,
        tmdb_episode=tmdb_episode or bangumi_sort or sort,
        tmdb_title=tmdb_title or sub.get("series_name") or sub.get("name", ""),
    ) + ext


async def write_episode_files(
    tmdb_ep: dict,
    *,
    season_number: int,
    episode_number: int,
    bangumi_ep_id: int | None,
    show_name: str,
    original_name: str,
    bangumi_subject_name: str,
    studios: list[str] | None = None,
    rating: float = 0.0,
    output_dir: str = ".",
    thumb_source: str = "tmdb",
    file_stem: str = "",
) -> dict:
    """Download episode thumbnail and generate ``.nfo`` — no API calls.

    All metadata must already be extracted into *tmdb_ep* before
    calling.  Used by both the RSS flow (:func:`~.metadata_builder.generate_metadata`)
    and the torrent batch flow (:func:`~my_anime_manager.services.batch_service.generate_metadata_collection`).

    Args:
        tmdb_ep: Normalised dict with keys ``name``, ``overview``,
            ``air_date``, ``runtime``, ``still_path``.
        season_number:  Season number written into the NFO.
        episode_number: Episode number written into the NFO.
        bangumi_ep_id:  Bangumi episode ID (or ``None``).
        show_name:      Show title → ``<showtitle>``.
        original_name:  Original name → ``<originaltitle>``.
        bangumi_subject_name: Bangumi subject name (fallback for file naming).
        studios:        Network / studio names.
        output_dir:     Directory to write NFO and thumbnail into.
        thumb_source:   ``"tvdb"`` or ``"tmdb"`` controls which CDN is used.
        file_stem:      Base filename (without extension) from path template.
            If empty, falls back to ``{bangumi_subject_name} {ep:02d}``.

    Returns:
        ``{"nfo_path": str, "thumb_path": str}``.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # ── Thumbnail ───────────────────────────────────────────────────
    if file_stem:
        thumb_base = file_stem
    else:
        thumb_base = f"{bangumi_subject_name or show_name} {episode_number:02d}"
    thumb_path = ""
    still = tmdb_ep.get("still_path", "")
    if still:
        try:
            if thumb_source == "tvdb":
                from .images import download_tvdb_episode_thumb
                thumb_path = await download_tvdb_episode_thumb(
                    still, output_dir, thumb_base,
                ) or ""
            else:
                thumb_path = await download_episode_thumb(
                    still, output_dir, thumb_base,
                ) or ""
        except Exception:
            logger.exception("thumbnail download failed (non-fatal)")

    # ── Episode NFO ─────────────────────────────────────────────────
    nfo_path = generate_episode_nfo(
        show_name=show_name,
        original_name=original_name,
        episode_name=tmdb_ep.get("name", ""),
        plot=tmdb_ep.get("overview", ""),
        air_date=tmdb_ep.get("air_date", ""),
        runtime=tmdb_ep.get("runtime", 0),
        season_number=season_number,
        episode_number=episode_number,
        bangumi_ep_id=bangumi_ep_id,
        bangumi_subject_name=bangumi_subject_name or show_name,
        directors=tmdb_ep.get("directors", []),
        writers=tmdb_ep.get("writers", []),
        actors=tmdb_ep.get("guest_stars", []),
        thumb_path=Path(thumb_path).name if thumb_path else "",
        studios=studios or [],
        rating=rating,
        output_dir=output_dir,
        tvdb_ep_id=tmdb_ep.get("tvdb_ep_id", 0),
        file_stem=file_stem,
    )

    return {"nfo_path": nfo_path, "thumb_path": thumb_path}


# ═══════════════════════════════════════════════════════════════════════
# Batch NFO generator — torrent pre-download orchestration
# ═══════════════════════════════════════════════════════════════════════

async def batch_nfo_generator(
    pre_path: str,
    episodes: list[dict],
    series_name: str = "",
) -> dict:
    """Generate NFO files + images for a batch of episodes.

    Pre-fetches all metadata (BGM episodes, TVDB series, TMDB show
    details, TMDB season episodes), caches per unique ID, then
    generates tvshow.nfo / season.nfo / episode.nfo using the path
    template from ``config.RSS_PATH_TEMPLATE``.

    Args:
        pre_path: Base directory prepended to the formatted template path.
        episodes: List of episode dicts, each with keys:
            ``bangumi_subject_id``, ``bangumi_episode_sort``,
            ``tvdb_id``, ``tvdb_season``, ``tvdb_episode``,
            ``tmdb_id``, ``tmdb_season``, ``tmdb_episode``.

    Returns:
        ``{"nfoGenerated": int, "imagesDownloaded": int}``.
    """
    import asyncio
    from ... import config
    from ...clients import tmdb as tmdb_client
    from .. import tmdb as tmdb_service
    from ..enrich import _get_bangumi_episodes
    from ..tvdb import fetch_tvdb_series_episodes
    from .images import (
        download_episode_thumb, download_show_images, download_season_poster,
        download_tvdb_episode_thumb,
    )
    from .nfo_xml import (
        generate_episode_nfo, generate_tv_show_nfo, generate_season_nfo,
    )

    if not episodes:
        return {"nfoGenerated": 0, "imagesDownloaded": 0}

    template = config.RSS_PATH_TEMPLATE

    # ── Phase 1: Collect unique IDs ───────────────────────────────────
    unique_bgm_ids = {ep["bangumi_subject_id"] for ep in episodes}
    unique_tvdb_ids = {ep["tvdb_id"] for ep in episodes if ep.get("tvdb_id")}
    unique_tmdb_ids = {ep["tmdb_id"] for ep in episodes}
    unique_tmdb_seasons = {(ep["tmdb_id"], ep["tmdb_season"]) for ep in episodes}

    # ── Phase 2: Pre-fetch all data (parallel where possible) ─────────
    bgm_cache: dict[int, list[dict]] = {}
    tvdb_cache: dict[int, dict] = {}
    tmdb_show_cache: dict[int, dict] = {}
    tmdb_season_cache: dict[tuple, dict] = {}

    # BGM subject names and full subject data
    bgm_subject_cache: dict[int, str] = {}
    bgm_subject_data_cache: dict[int, dict] = {}

    # BGM episodes
    async def _fetch_bgm(bgm_id: int):
        try:
            eps = await _get_bangumi_episodes(bgm_id)
            bgm_cache[bgm_id] = eps or []
        except Exception:
            logger.exception("BGM episodes fetch failed: %d", bgm_id)
            bgm_cache[bgm_id] = []
        # Also fetch subject name + full data
        try:
            from ...clients import bangumi as bgm_client
            subject = await bgm_client.get_subject(bgm_id)
            bgm_subject_cache[bgm_id] = subject.get("name_cn") or subject.get("name", str(bgm_id))
            bgm_subject_data_cache[bgm_id] = subject
        except Exception:
            bgm_subject_cache[bgm_id] = str(bgm_id)

    # TVDB series
    async def _fetch_tvdb(tid: int):
        try:
            data = await fetch_tvdb_series_episodes(tid)
            if data:
                tvdb_cache[tid] = data
        except Exception:
            logger.exception("TVDB series fetch failed: %d", tid)

    # TMDB show detail
    async def _fetch_tmdb_show(tid: int):
        try:
            resp = await tmdb_client.get_tv_detail(tid, language="zh-CN")
            detail = resp.json()
            tmdb_show_cache[tid] = {
                "title": detail.get("name", ""),
                "original_title": detail.get("original_name", ""),
                "overview": detail.get("overview", ""),
                "genres": [g.get("name", "") for g in detail.get("genres", [])],
                "studios": (
                    [s.get("name", "") for s in detail.get("created_by", [])]
                    or [n.get("name", "") for n in detail.get("networks", [])]
                ),
                "rating": detail.get("vote_average", 0) or 0,
                "first_air_date": detail.get("first_air_date", ""),
                "status": detail.get("status", ""),
            }
        except Exception:
            logger.exception("TMDB show fetch failed: %d", tid)

    # TMDB season episodes (zh-CN for Chinese plot/names)
    async def _fetch_tmdb_season(tid: int, sn: int):
        try:
            season_map = await tmdb_service.build_season_episode_map(tid, language="zh-CN")
            tmdb_season_cache[(tid, sn)] = season_map
        except Exception:
            logger.exception("TMDB season fetch failed: %d S%d", tid, sn)

    # Run all pre-fetches concurrently
    tasks = []
    for bid in unique_bgm_ids:
        tasks.append(_fetch_bgm(bid))
    for tid in unique_tvdb_ids:
        tasks.append(_fetch_tvdb(tid))
    for tid in unique_tmdb_ids:
        tasks.append(_fetch_tmdb_show(tid))
    for (tid, sn) in unique_tmdb_seasons:
        tasks.append(_fetch_tmdb_season(tid, sn))

    await asyncio.gather(*tasks, return_exceptions=True)

    # ── Phase 3: Generate NFO per episode ─────────────────────────────
    nfo_count = 0
    img_count = 0
    seen_show: set[str] = set()
    seen_season: set[str] = set()

    # Collect per-episode data for deferred thumbnail + NFO generation
    pending_eps: list[dict] = []
    thumb_coros: list = []  # (coroutine, index into pending_eps)

    for ep in episodes:
        bgm_id = ep["bangumi_subject_id"]
        bgm_sort = ep.get("bangumi_episode_sort", 0)
        tvdb_id = ep.get("tvdb_id") or 0
        tvdb_season = ep.get("tvdb_season")  # keep None — 0 is valid (Specials)
        tvdb_ep = ep.get("tvdb_episode") or 0
        tmdb_id = ep["tmdb_id"]
        tmdb_season = ep.get("tmdb_season", 0)
        tmdb_ep_num = ep.get("tmdb_episode", 0)

        # ── Resolve metadata from caches ──
        show = tmdb_show_cache.get(tmdb_id, {})
        tmdb_title = show.get("title", str(tmdb_id))

        # BGM episode data
        bgm_ep_name = ""
        bgm_ep_name_cn = ""
        bangumi_ep_val = bgm_sort
        for e in bgm_cache.get(bgm_id, []):
            if (e.get("sort") or e.get("ep", 0)) == bgm_sort:
                bangumi_ep_val = e.get("ep") or bgm_sort
                bgm_ep_name = (e.get("name") or "").strip()
                bgm_ep_name_cn = (e.get("name_cn") or "").strip()
                break

        # TVDB episode data
        tvdb_ep_data: dict = {}
        if tvdb_id and tvdb_id in tvdb_cache:
            tvdb_seasons = tvdb_cache[tvdb_id].get("seasons", {})
            _lookup_tvdb_s = tvdb_season if tvdb_season is not None else tmdb_season
            season_data = tvdb_seasons.get(str(_lookup_tvdb_s), {})
            for tv_ep in season_data.get("episodes", []):
                if tv_ep.get("epNum") == tvdb_ep:
                    tvdb_ep_data = {
                        "tvdb_ep_id": tv_ep.get("tvdbId", 0),
                        "site_rating": tv_ep.get("siteRating", 0) or 0,
                        "overview": tv_ep.get("overview", ""),
                        "name": tv_ep.get("name", ""),
                        "air_date": tv_ep.get("airDate", ""),
                        "runtime": tv_ep.get("runtime", 0),
                        "still_path": tv_ep.get("stillPath", ""),
                    }
                    break

        # TMDB episode data
        tmdb_ep_data: dict = {}
        season_map = tmdb_season_cache.get((tmdb_id, tmdb_season), {})
        season_eps = season_map.get(str(tmdb_season), season_map.get(tmdb_season, {}))
        for tm_ep in season_eps.get("episodes", []):
            if tm_ep.get("epNum") == tmdb_ep_num:
                tmdb_ep_data = dict(tm_ep)
                break

        # Merge: TMDB base, TVDB overrides (rating, tvdb_ep_id), BGM name_cn
        still_source = "tmdb"
        merged_ep: dict = dict(tmdb_ep_data)
        # Normalise: build_season_episode_map returns camelCase keys
        # (stillPath, airDate) — unify to snake_case for downstream code
        if "stillPath" in merged_ep:
            merged_ep.setdefault("still_path", merged_ep["stillPath"])
        if tvdb_ep_data:
            merged_ep.setdefault("tvdb_ep_id", tvdb_ep_data.get("tvdb_ep_id", 0))
            if tvdb_ep_data.get("site_rating"):
                merged_ep["site_rating"] = tvdb_ep_data["site_rating"]
            if tvdb_ep_data.get("overview") and not merged_ep.get("overview"):
                merged_ep["overview"] = tvdb_ep_data["overview"]
            if tvdb_ep_data.get("still_path") and not merged_ep.get("still_path"):
                merged_ep["still_path"] = tvdb_ep_data["still_path"]
                still_source = "tvdb"
        if bgm_ep_name_cn:
            merged_ep["name"] = bgm_ep_name_cn
        elif bgm_ep_name and not merged_ep.get("name"):
            merged_ep["name"] = bgm_ep_name

        # ── Episode plot fallback (zh-CN via TMDB/TVDB/Bangumi+DeepSeek) ──
        if not merged_ep.get("overview"):
            try:
                from .plot_fallback import resolve_episode_plot
                zh_plot = await resolve_episode_plot(
                    tmdb_id=tmdb_id,
                    tvdb_id=tvdb_id,
                    tvdb_season=tvdb_season,
                    tvdb_ep=tvdb_ep,
                    tmdb_season=tmdb_season,
                    tmdb_ep_num=tmdb_ep_num,
                    bangumi_id=bgm_id,
                    bangumi_sort=bgm_sort,
                )
                if zh_plot:
                    merged_ep["overview"] = zh_plot
            except Exception:
                pass  # non-fatal

        # ── Compute paths via template ──
        sub = {
            "name": tmdb_title,
            "series_name": series_name or tmdb_title,
            "bgm": {
                "subject_name": tmdb_title,
                "season": 1,
            },
            "tvdb": {"season": tvdb_season if tvdb_season is not None else tmdb_season},
            "tmdb": {"season": tmdb_season},
        }
        rel_path = format_download_path(
            template, sub,
            bangumi_sort=bgm_sort, bangumi_ep=int(bangumi_ep_val),
            tvdb_episode=tvdb_ep, tmdb_episode=tmdb_ep_num,
            tmdb_title=tmdb_title,
        ).lstrip("/")
        file_stem = Path(rel_path).stem
        season_dir = Path(pre_path) / Path(rel_path).parent
        show_dir = season_dir.parent
        season_dir.mkdir(parents=True, exist_ok=True)

        # ── tvshow.nfo + images (once per show_dir) ──
        show_key = str(show_dir)
        if show_key not in seen_show:
            seen_show.add(show_key)
            generate_tv_show_nfo(
                title=show.get("title", str(tmdb_id)),
                original_title=show.get("original_title", ""),
                plot=show.get("overview", ""),
                output_dir=str(show_dir),
                tvdb_id=tvdb_id,
                tmdb_id=tmdb_id,
            )
            nfo_count += 1
            logger.info("   tvshow.nfo → %s", show_dir / "tvshow.nfo")
            imgs = await download_show_images(tmdb_id, str(show_dir))
            img_count += sum(1 for v in imgs.values() if v)

        # BGM subject name (for season.nfo originaltitle)
        bgm_subject_name = bgm_subject_cache.get(bgm_id, tmdb_title)

        # BGM episode ID (for episode.nfo bangumiid)
        bgm_ep_id = None
        for e in bgm_cache.get(bgm_id, []):
            if (e.get("sort") or e.get("ep", 0)) == bgm_sort:
                bgm_ep_id = e.get("id")
                break

        # ── season.nfo + poster (once per season_dir) ──
        season_key = str(season_dir)
        if season_key not in seen_season:
            seen_season.add(season_key)
            effective_tvdb_season = tvdb_season if tvdb_season is not None else tmdb_season

            # Resolve season plot: TMDB overview → BGM summary fallback
            season_plot = show.get("overview", "")
            if not season_plot:
                subject_data = bgm_subject_data_cache.get(bgm_id, {})
                bgm_summary = subject_data.get("summary", "")
                if bgm_summary:
                    try:
                        from .plot_fallback import resolve_season_plot
                        resolved = await resolve_season_plot(bgm_summary)
                        if resolved:
                            season_plot = resolved
                    except Exception:
                        pass

            generate_season_nfo(
                title=f"Season {effective_tvdb_season}",
                original_title=bgm_subject_name,
                plot=season_plot,
                premiered=show.get("first_air_date", ""),
                season_number=int(effective_tvdb_season),
                bangumi_id=bgm_id,
                output_dir=str(season_dir),
                tvdb_season_id=tvdb_id,
            )
            nfo_count += 1
            logger.info("   season.nfo → %s", season_dir / "season.nfo")
            # Season poster from BGM images if available
            bgm_subject_images = None
            for e in bgm_cache.get(bgm_id, []):
                if isinstance(e, dict) and "images" in e:
                    bgm_subject_images = e.get("images")
                    break
            if bgm_subject_images:
                try:
                    await download_season_poster(
                        {"images": bgm_subject_images},
                        str(show_dir), int(effective_tvdb_season),
                    )
                    img_count += 1
                except Exception:
                    pass

        # ── Episode: collect thumb coroutine + metadata ──
        # Season and episode must be atomic: either both from TVDB
        # or both from TMDB.  Mixing sources (e.g. TVDB season + TMDB
        # episode) produces wrong results when the two databases use
        # different season boundaries.
        _use_tvdb = (tvdb_season is not None) and bool(tvdb_ep)
        eff_season = tvdb_season if _use_tvdb else tmdb_season
        eff_episode = tvdb_ep if _use_tvdb else tmdb_ep_num

        # Collect thumbnail download coroutine (deferred to Phase 3b)
        still = merged_ep.get("still_path", "")
        if still:
            if still_source == "tvdb":
                thumb_coros.append((
                    download_tvdb_episode_thumb(still, str(season_dir), file_stem),
                    len(pending_eps),
                ))
            else:
                thumb_coros.append((
                    download_episode_thumb(still, str(season_dir), file_stem),
                    len(pending_eps),
                ))

        pending_eps.append({
            "show": show,
            "bgm_ep_name": bgm_ep_name,
            "bgm_ep_name_cn": bgm_ep_name_cn,
            "bgm_subject_name": bgm_subject_name,
            "bgm_ep_id": bgm_ep_id,
            "merged_ep": merged_ep,
            "eff_season": eff_season,
            "eff_episode": eff_episode,
            "season_dir": str(season_dir),
            "file_stem": file_stem,
            "tmdb_id": tmdb_id,
            "has_thumb": bool(still),
        })

    # ── Phase 3b: Download all thumbnails concurrently ────────────────
    thumb_results: dict[int, str] = {}
    if thumb_coros:
        coros, indices = zip(*[(c, i) for c, i in thumb_coros])
        raw = await asyncio.gather(*coros, return_exceptions=True)
        for idx, result in zip(indices, raw):
            if isinstance(result, Exception):
                continue
            if result:
                thumb_results[idx] = str(result)
                img_count += 1

    # ── Phase 3c: Generate episode NFOs ───────────────────────────────
    for i, rec in enumerate(pending_eps):
        thumb_path = thumb_results.get(i, "")
        generate_episode_nfo(
            show_name=rec["show"].get("title", str(rec["tmdb_id"])),
            original_name=rec["bgm_ep_name"] or rec["merged_ep"].get("name", ""),
            episode_name=rec["bgm_ep_name_cn"] or rec["merged_ep"].get("name", ""),
            plot=rec["merged_ep"].get("overview", ""),
            air_date=rec["merged_ep"].get("airDate", rec["merged_ep"].get("air_date", "")),
            runtime=rec["merged_ep"].get("runtime", 0),
            season_number=int(rec["eff_season"]),
            episode_number=rec["eff_episode"],
            bangumi_ep_id=rec["bgm_ep_id"],
            bangumi_subject_name=rec["bgm_subject_name"],
            thumb_path=Path(thumb_path).name if thumb_path else "",
            rating=rec["merged_ep"].get("site_rating") or rec["merged_ep"].get("voteAverage", rec["merged_ep"].get("vote_average", 0)) or 0,
            output_dir=rec["season_dir"],
            tvdb_ep_id=rec["merged_ep"].get("tvdb_ep_id", 0),
            file_stem=rec["file_stem"],
        )
        nfo_count += 1
        logger.info("   episode.nfo → %s", Path(rec["season_dir"]) / f"{rec['file_stem']}.nfo")

    # ── Phase 4: Clear caches, return summary ──
    bgm_cache.clear()
    bgm_subject_data_cache.clear()
    tvdb_cache.clear()
    tmdb_show_cache.clear()
    tmdb_season_cache.clear()

    return {"nfoGenerated": nfo_count, "imagesDownloaded": img_count}
