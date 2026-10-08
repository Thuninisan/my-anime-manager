"""NFO orchestration: acquire provider data, resolve episodes, download assets, serialize.

Legacy RSS/batch inputs cross the domain adapters before metadata resolution.
The XML writer consumes only ResolvedEpisode; compatibility calls are re-exported.
"""

import logging
from pathlib import Path

from ... import config
from .episode_compat import write_episode_files

logger = logging.getLogger(__name__)


def sanitize_path_name(name: str) -> str:
    """Replace ``/`` and ``\\`` with a space, collapsing whitespace.

    Since ``/`` is always a path separator on Linux/macOS and ``\\`` is
    always one on Windows, neither can appear in a file or directory
    name.  Replacing with a space keeps the name readable (e.g.
    "Fate stay night") without creating spurious directory levels.
    """
    sanitized = name.replace("/", " ").replace("\\", " ")
    return " ".join(sanitized.split())


def format_download_path(
    template: str, sub: dict, sort: int = 0, ext: str = "",
    bangumi_sort: int | None = None, bangumi_ep: int | None = None,
    tvdb_episode: int | None = None, tmdb_episode: int | None = None,
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
    effective_sort = bangumi_sort if bangumi_sort is not None else sort
    effective_bangumi_episode = bangumi_ep if bangumi_ep is not None else effective_sort
    return template.format(
        series_name=sanitize_path_name(sub.get("series_name") or sub.get("name", "")),
        bangumi_title=sanitize_path_name(bgm.get("subject_name") or sub.get("name", "")),
        bgm_season=_bgm_s,
        tvdb_season=_tvdb_s if _tvdb_s is not None else _bgm_s,
        tmdb_season=_tmdb_s if _tmdb_s is not None else _bgm_s,
        sort=effective_sort,
        bangumi_sort=effective_sort,
        bangumi_ep=effective_bangumi_episode,
        tvdb_episode=tvdb_episode if tvdb_episode is not None else effective_bangumi_episode,
        tmdb_episode=tmdb_episode if tmdb_episode is not None else effective_sort,
        tmdb_title=sanitize_path_name(tmdb_title or sub.get("series_name") or sub.get("name", "")),
    ) + ext


# ═══════════════════════════════════════════════════════════════════════
# Batch NFO generator — torrent pre-download orchestration
# ═══════════════════════════════════════════════════════════════════════

async def batch_nfo_generator(
    pre_path: str,
    episodes: list[dict],
    series_name: str = "",
    overwrite: bool = False,
    metadata_ctx=None,
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
        overwrite: Rewrite episode NFO + thumbs even if they exist (regen).

    Returns:
        ``{"nfoGenerated": int, "imagesDownloaded": int}``.
    """
    import asyncio
    from ... import config
    from .images import (
        download_episode_thumb, download_show_images, download_season_poster,
        download_tvdb_episode_thumb,
    )
    from .nfo_xml import (
        generate_episode_nfo as write_resolved_episode_nfo, generate_tv_show_nfo, generate_season_nfo,
    )

    if not episodes:
        return {"nfoGenerated": 0, "imagesDownloaded": 0}
    from .metadata_context import MetadataContext
    metadata_ctx = metadata_ctx or MetadataContext()

    template = config.RSS_PATH_TEMPLATE

    from ...domain.episode_metadata_adapters import (
        legacy_batch_episode_mapping, metadata_candidates_from_catalogs, bind_legacy_episode_ids,
    )
    from ..episode_metadata_resolver import resolve_nfo_episode
    mappings = [legacy_batch_episode_mapping(episode) for episode in episodes]

    # ── Phase 1: Collect already determined identities ───────────────
    unique_bgm_ids = {mapping["bangumi"]["subject_id"] for mapping in mappings if mapping["bangumi"]["subject_id"]}
    unique_tvdb_ids = {mapping["tvdb"]["series_id"] for mapping in mappings if mapping["tvdb"]["series_id"]}
    unique_tmdb_ids = {mapping["tmdb"]["series_id"] for mapping in mappings if mapping["tmdb"]["series_id"]}
    unique_tmdb_seasons = unique_tmdb_ids

    # ── Phase 2: Pre-fetch all data (parallel where possible) ─────────
    bgm_cache: dict[int, list[dict]] = {}
    tvdb_cache: dict[int, dict] = {}
    tmdb_show_cache: dict[int, dict] = {}

    # BGM subject names and full subject data
    bgm_subject_cache: dict[int, str] = {}
    bgm_subject_data_cache: dict[int, dict] = {}

    # BGM episodes
    async def _fetch_bgm(bgm_id: int):
        try:
            eps = await metadata_ctx.get_bgm_episodes(bgm_id)
            bgm_cache[bgm_id] = eps or []
        except Exception:
            logger.exception("BGM episodes fetch failed: %d", bgm_id)
            bgm_cache[bgm_id] = []
        # Also fetch subject name + full data
        try:
            subject = await metadata_ctx.get_bgm_subject(bgm_id)
            bgm_subject_cache[bgm_id] = subject.get("name_cn") or subject.get("name", str(bgm_id))
            bgm_subject_data_cache[bgm_id] = subject
        except Exception:
            bgm_subject_cache[bgm_id] = str(bgm_id)

    # TVDB series
    async def _fetch_tvdb(tid: int):
        try:
            data = await metadata_ctx.get_tvdb_series(tid)
            if data:
                tvdb_cache[tid] = data
        except Exception:
            logger.exception("TVDB series fetch failed: %d", tid)

    # TMDB show detail
    async def _fetch_tmdb_show(tid: int):
        try:
            detail = await metadata_ctx.get_tmdb_detail(tid, "zh-CN")
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
    async def _fetch_tmdb_season(tid: int):
        try:
            season_map = await metadata_ctx.get_tmdb_season_map(tid, "zh-CN")
        except Exception:
            logger.exception("TMDB season fetch failed: %d", tid)

    # Run all pre-fetches concurrently
    tasks = []
    for bid in unique_bgm_ids:
        tasks.append(_fetch_bgm(bid))
    for tid in unique_tvdb_ids:
        tasks.append(_fetch_tvdb(tid))
    for tid in unique_tmdb_ids:
        tasks.append(_fetch_tmdb_show(tid))
    for tid in unique_tmdb_seasons:
        tasks.append(_fetch_tmdb_season(tid))

    await asyncio.gather(*tasks, return_exceptions=True)

    # ── Phase 3: Generate NFO per episode ─────────────────────────────
    nfo_count = 0
    img_count = 0
    seen_show = metadata_ctx.show_assets_done
    seen_season = metadata_ctx.season_assets_done

    # Collect per-episode data for deferred thumbnail + NFO generation
    pending_eps: list[dict] = []
    thumb_coros: list = []  # (coroutine, index into pending_eps)

    for ep, mapping in zip(episodes, mappings):
        bgm_id = mapping["bangumi"]["subject_id"]
        bgm_sort = mapping["bangumi"]["episode_absolute"]
        tvdb_id = mapping["tvdb"]["series_id"] or 0
        tvdb_season = mapping["tvdb"]["season_number"]  # keep None — 0 is valid (Specials)
        tvdb_ep = mapping["tvdb"]["episode_number"]
        tmdb_id = mapping["tmdb"]["series_id"] or 0
        tmdb_season = mapping["tmdb"]["season_number"]
        tmdb_ep_num = mapping["tmdb"]["episode_number"]
        ep_context = f"BGM {bgm_id} 第{bgm_sort}集 / TMDB {tmdb_id} S{int(tmdb_season or 0):02d}E{int(tmdb_ep_num or 0):02d}"

        # ── Resolve metadata from caches ──
        show = tmdb_show_cache.get(tmdb_id, {})
        tmdb_title = show.get("title", str(tmdb_id))

        # Raw provider payloads stop at the catalog compatibility boundary.
        is_legacy = ep.get("episode_mapping") is None or ep.get("_legacy_episode_mapping", False)
        season_map = await metadata_ctx.get_tmdb_season_map(tmdb_id, "zh-CN") if tmdb_id else {}
        if metadata_ctx.preview_snapshot is not None:
            from ..torrent.preview_session import metadata_candidates
            candidates = metadata_candidates(metadata_ctx.preview_snapshot, mapping)
        else:
            candidates = metadata_candidates_from_catalogs(
                mapping, season_map, tvdb_cache.get(tvdb_id, {}),
                bgm_cache.get(bgm_id, []), legacy=is_legacy,
            )
        if is_legacy:
            mapping = bind_legacy_episode_ids(mapping, candidates, bgm_cache.get(bgm_id, []))
        bangumi_ep_val = mapping["bangumi"]["episode_number"]
        if is_legacy and bangumi_ep_val is None:
            bangumi_ep_val = bgm_sort  # compatibility path naming only
        resolved = await resolve_nfo_episode(
            mapping, candidates,
            show_name=bgm_subject_cache.get(bgm_id) or tmdb_title,
            context=ep_context, metadata_ctx=metadata_ctx,
        )
        still_source = resolved["provenance"]["still_path"]

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
            bangumi_sort=bgm_sort, bangumi_ep=int(bangumi_ep_val if bangumi_ep_val is not None else 0),
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
            show_nfo_exists = (show_dir / "tvshow.nfo").exists()
            if not show_nfo_exists:
                logger.info("NFO [%s tvshow.nfo] 标题/原名/简介：TMDB；TMDB ID：TMDB；TVDB ID：TVDB", show_dir)
            generate_tv_show_nfo(
                title=show.get("title", str(tmdb_id)),
                original_title=show.get("original_title", ""),
                plot=show.get("overview", ""),
                output_dir=str(show_dir),
                tvdb_id=tvdb_id,
                tmdb_id=tmdb_id,
            )
            nfo_count += int(not show_nfo_exists)
            logger.info("   tvshow.nfo → %s", show_dir / "tvshow.nfo")
            imgs = await download_show_images(tmdb_id, str(show_dir)) if tmdb_id else {}
            img_count += sum(1 for v in imgs.values() if v)

        # BGM subject name (for season.nfo originaltitle)
        bgm_subject_name = bgm_subject_cache.get(bgm_id, tmdb_title)

        # ── season.nfo + poster (once per season_dir) ──
        season_key = str(season_dir)
        if season_key not in seen_season:
            seen_season.add(season_key)
            effective_tvdb_season = tvdb_season if tvdb_season is not None else tmdb_season

            # Resolve season plot: TMDB overview → BGM summary fallback
            season_plot = show.get("overview", "")
            if season_plot:
                logger.info("NFO [%s season.nfo 简介] TMDB：命中", season_dir)
            if not season_plot:
                logger.info("NFO [%s season.nfo 简介] TMDB：无简介，尝试 Bangumi", season_dir)
                subject_data = bgm_subject_data_cache.get(bgm_id, {})
                bgm_summary = subject_data.get("summary", "")
                if bgm_summary:
                    try:
                        from .plot_fallback import resolve_season_plot
                        resolved = await resolve_season_plot(bgm_summary, context=str(season_dir))
                        if resolved:
                            season_plot = resolved
                    except Exception:
                        pass

            season_nfo_exists = (season_dir / "season.nfo").exists()
            if not season_nfo_exists:
                logger.info("NFO [%s season.nfo] 标题/季号：季映射；原名/Bangumi ID：Bangumi；播出日期：TMDB", season_dir)
            generate_season_nfo(
                title=f"Season {effective_tvdb_season}",
                original_title=bgm_subject_name,
                plot=season_plot,
                premiered=show.get("first_air_date", ""),
                season_number=resolved["season_number"],
                bangumi_id=bgm_id,
                output_dir=str(season_dir),
                tvdb_season_id=tvdb_id,
            )
            nfo_count += int(not season_nfo_exists)
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
        # Coordinates and still provenance are already resolved.
        still = resolved["metadata"]["still_path"]
        if still:
            if still_source == "tvdb":
                thumb_coros.append((
                    download_tvdb_episode_thumb(
                        still, str(season_dir), file_stem, overwrite=overwrite,
                    ),
                    len(pending_eps),
                ))
            else:
                thumb_coros.append((
                    download_episode_thumb(
                        still, str(season_dir), file_stem, overwrite=overwrite,
                    ),
                    len(pending_eps),
                ))

        pending_eps.append({
            "context": ep_context,
            "title_source": resolved["provenance"]["title"],
            "plot_source": resolved["provenance"]["plot"],
            "show": show,
            "bgm_subject_name": bgm_subject_name,
            "resolved": resolved,
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
        episode_nfo_exists = (Path(rec["season_dir"]) / f"{rec['file_stem']}.nfo").exists()
        if overwrite or not episode_nfo_exists:
            logger.info("NFO [%s] 字段来源：标题=%s；简介=%s；"
                        "原名=Bangumi→标题；日期/时长=TMDB；评分=TVDB→TMDB；"
                        "季集编号=TVDB→TMDB；缩略图=%s",
                        rec["context"], rec["title_source"], rec["plot_source"],
                        "已下载" if thumb_path else "无")
        write_resolved_episode_nfo(
            rec["resolved"],
            show_name=rec["show"].get("title", str(rec["tmdb_id"])),
            bangumi_subject_name=rec["bgm_subject_name"],
            thumb_path=Path(thumb_path).name if thumb_path else "",
            output_dir=rec["season_dir"], file_stem=rec["file_stem"], overwrite=overwrite,
        )
        nfo_count += int(overwrite or not episode_nfo_exists)
        logger.info("   episode.nfo → %s", Path(rec["season_dir"]) / f"{rec['file_stem']}.nfo")

    # ── Phase 4: Clear caches, return summary ──
    bgm_cache.clear()
    bgm_subject_data_cache.clear()
    tvdb_cache.clear()
    tmdb_show_cache.clear()

    return {"nfoGenerated": nfo_count, "episodesProcessed": len(pending_eps), "imagesDownloaded": img_count,
            "episodePaths": [str((Path(rec["season_dir"]) / rec["file_stem"]).relative_to(pre_path)) for rec in pending_eps]}
