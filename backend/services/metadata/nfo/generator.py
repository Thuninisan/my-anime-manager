"""NFO orchestration: acquire provider data, resolve episodes, download assets, serialize.

RSS, torrent and batch consumers supply canonical episode mappings.
The XML writer consumes only ResolvedEpisode.
"""

import logging
from pathlib import Path

from ...media.paths import format_download_path

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Batch NFO generator — torrent pre-download orchestration
# ═══════════════════════════════════════════════════════════════════════

async def batch_nfo_generator(
    pre_path: str,
    episodes: list[dict],
    series_name: str = "",
    overwrite: bool = False,
    metadata_ctx=None,
    *,
    overwrite_assets: bool = False,
    download_images: bool = True,
    download_episode_images: bool | None = None,
    thumbnail_results: list[dict] | None = None,
    refresh_results: list[dict] | None = None,
    path_template: str | None = None,
) -> dict:
    """Generate NFO files + images for a batch of episodes.

    Pre-fetches all metadata (BGM episodes, TVDB series, TMDB show
    details, TMDB season episodes), caches per unique ID, then
    generates tvshow.nfo / season.nfo / episode.nfo using the path
    template from ``config.RSS_PATH_TEMPLATE``.

    Args:
        pre_path: Base directory prepended to the formatted template path.
        episodes: Canonical entries containing ``episode_mapping``.
        overwrite: Rewrite episode NFO + thumbs even if they exist (regen).
        overwrite_assets: Also rewrite existing show/season NFOs.
        download_images: Disable image requests and reuse local thumbnails.
        download_episode_images: 单独控制单集缩略图，默认沿用 download_images。
        thumbnail_results: 记录缩略图的成功、无图片与下载失败状态。
        refresh_results: Collect per-file outcomes and continue after write errors.
        path_template: Frozen template for a background refresh job.

    Returns:
        ``{"nfoGenerated": int, "imagesDownloaded": int}``.
    """
    import asyncio
    from backend import config
    from .images import download_episode_thumb, download_show_images, download_season_poster, download_tvdb_episode_thumb
    from .nfo_xml import generate_episode_nfo as write_resolved_episode_nfo, generate_tv_show_nfo, generate_season_nfo

    if not episodes:
        return {"nfoGenerated": 0, "imagesDownloaded": 0}
    download_thumbnails = download_images if download_episode_images is None else download_episode_images
    from ..context import MetadataContext
    metadata_ctx = metadata_ctx or MetadataContext()

    template = path_template if path_template is not None else config.RSS_PATH_TEMPLATE

    def write_file(kind, path, writer, **kwargs):
        if refresh_results is None:
            return writer(**kwargs)
        try:
            existed = path.exists()
            output = writer(**kwargs)
        except Exception as exc:
            logger.exception("NFO refresh failed: %s", path)
            refresh_results.append({"kind": kind, "path": str(path), "status": "failed", "message": str(exc)})
            return None
        refresh_results.append({"kind": kind, "path": str(path), "status": "success",
                                "message": "已覆盖" if existed else "已生成"})
        return output

    from ....domain.episode_metadata_adapters import metadata_candidates_from_catalogs
    from ..resolution import resolve_nfo_episode
    mappings = [episode["episode_mapping"] for episode in episodes]

    # ── Phase 1: Collect already determined identities ───────────────
    unique_bgm_ids = {mapping["bangumi"]["subject_id"] for mapping in mappings if mapping["bangumi"]["subject_id"]}
    unique_tvdb_ids = {mapping["tvdb"]["series_id"] for mapping in mappings if mapping["tvdb"]["series_id"]}
    unique_tmdb_ids = {mapping["tmdb"]["series_id"] for mapping in mappings if mapping["tmdb"]["series_id"]}
    unique_tmdb_seasons = unique_tmdb_ids
    if metadata_ctx.preview_snapshot is not None:
        for mapping in mappings:
            tm = mapping["tmdb"]
            if tm["series_id"] and tm["season_number"] is not None:
                metadata_ctx.tmdb_selected_seasons.setdefault(tm["series_id"], set()).add(tm["season_number"])

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
        bangumi_episode_sort = mapping["bangumi"]["episode_absolute"]
        tvdb_id = mapping["tvdb"]["series_id"] or 0
        tvdb_season = mapping["tvdb"]["season_number"]  # keep None — 0 is valid (Specials)
        tvdb_episode_number = mapping["tvdb"]["episode_number"]
        tmdb_id = mapping["tmdb"]["series_id"] or 0
        tmdb_season = mapping["tmdb"]["season_number"]
        tmdb_ep_num = mapping["tmdb"]["episode_number"]
        ep_context = f"BGM {bgm_id} 第{bangumi_episode_sort}集 / TMDB {tmdb_id} S{int(tmdb_season or 0):02d}E{int(tmdb_ep_num or 0):02d}"

        # ── Resolve metadata from caches ──
        show = tmdb_show_cache.get(tmdb_id, {})
        tmdb_title = show.get("title", str(tmdb_id))
        if refresh_results is not None and not show.get("title"):
            tmdb_title = series_name or tvdb_cache.get(tvdb_id, {}).get("name") or bgm_subject_cache.get(bgm_id, str(bgm_id))

        # Raw provider payloads stop at the catalog compatibility boundary.
        season_map = metadata_ctx.tmdb_season_maps.get((tmdb_id, "zh-CN"), {})
        if metadata_ctx.preview_snapshot is not None:
            from ....domain.preview_metadata import metadata_candidates
            candidates = metadata_candidates(metadata_ctx.preview_snapshot, mapping)
            localized = metadata_candidates_from_catalogs(mapping, season_map, {}, [])["tmdb"]
            if localized is not None:
                candidate = dict(candidates["tmdb"] or localized)
                # Localized text supplements the matched Japanese metadata.
                for field in ("title", "plot"):
                    if localized[field]:
                        candidate[field] = localized[field]
                candidates["tmdb"] = candidate
        else:
            candidates = metadata_candidates_from_catalogs(
                mapping, season_map, tvdb_cache.get(tvdb_id, {}),
                bgm_cache.get(bgm_id, []),
            )
        if refresh_results is not None and not any(candidates.get(provider) for provider in ("tmdb", "tvdb")):
            raise ValueError("未获取到该集的 TMDB/TVDB 元数据，已保留原 NFO")
        bangumi_ep_val = mapping["bangumi"]["episode_number"]
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
        if ep.get("download_path"):
            media_path = Path(ep["download_path"])
            file_stem = media_path.stem
            season_dir = media_path.parent
        else:
            rel_path = format_download_path(
                template, sub,
                bangumi_sort=bangumi_episode_sort, bangumi_ep=int(bangumi_ep_val if bangumi_ep_val is not None else 0),
                tvdb_episode=tvdb_episode_number, tmdb_episode=tmdb_ep_num,
                tmdb_title=tmdb_title,
            ).lstrip("/")
            file_stem = Path(rel_path).stem
            season_dir = Path(pre_path) / Path(rel_path).parent
        show_dir = season_dir.parent
        if refresh_results is not None:
            if not file_stem or any(part == ".." for part in season_dir.parts):
                raise ValueError("NFO 输出路径无效")
            if not ep.get("download_path") and not season_dir.resolve().is_relative_to(Path(pre_path).resolve()):
                raise ValueError("模板输出路径超出下载目录")
            if not ep.get("download_path") and not show_dir.resolve().is_relative_to(Path(pre_path).resolve()):
                raise ValueError("模板缺少作品目录，无法安全确定剧集 NFO 输出位置")
        season_dir.mkdir(parents=True, exist_ok=True)

        # ── tvshow.nfo + images (once per show_dir) ──
        show_key = str(show_dir.resolve()) if refresh_results is not None else str(show_dir)
        if show_key not in seen_show:
            seen_show.add(show_key)
            show_nfo_exists = (show_dir / "tvshow.nfo").exists()
            if not show_nfo_exists:
                logger.info("NFO [%s tvshow.nfo] 标题/原名/简介：TMDB；TMDB ID：TMDB；TVDB ID：TVDB", show_dir)
            if refresh_results is not None and not show.get("title"):
                refresh_results.append({"kind": "show", "path": str(show_dir / "tvshow.nfo"),
                                        "status": "failed", "message": "未获取到作品元数据，已保留原 NFO"})
            else:
                written = write_file("show", show_dir / "tvshow.nfo", generate_tv_show_nfo,
                    title=show.get("title", str(tmdb_id)),
                    original_title=show.get("original_title", ""),
                    plot=show.get("overview", ""),
                    output_dir=str(show_dir),
                    tvdb_id=tvdb_id,
                    tmdb_id=tmdb_id,
                    overwrite=overwrite_assets,
                )
                nfo_count += int(bool(written) and (overwrite_assets or not show_nfo_exists))
            imgs = await download_show_images(tmdb_id, str(show_dir)) if download_images and tmdb_id else {}
            img_count += sum(1 for v in imgs.values() if v)

        # BGM subject name (for season.nfo originaltitle)
        bgm_subject_name = bgm_subject_cache.get(bgm_id, tmdb_title)
        if refresh_results is not None and bgm_id not in bgm_subject_data_cache:
            bgm_subject_name = tmdb_title

        # ── season.nfo + poster (once per season_dir) ──
        season_key = str(season_dir.resolve()) if refresh_results is not None else str(season_dir)
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
                        from ..plots import resolve_season_plot
                        resolved_plot = await resolve_season_plot(bgm_summary, context=str(season_dir))
                        if resolved_plot:
                            season_plot = resolved_plot
                    except Exception:
                        pass

            season_nfo_exists = (season_dir / "season.nfo").exists()
            if not season_nfo_exists:
                logger.info("NFO [%s season.nfo] 标题/季号：季映射；原名/Bangumi ID：Bangumi；播出日期：TMDB", season_dir)
            written = write_file("season", season_dir / "season.nfo", generate_season_nfo,
                title=f"Season {effective_tvdb_season}",
                original_title=bgm_subject_name,
                plot=season_plot,
                premiered=show.get("first_air_date", ""),
                season_number=resolved["season_number"],
                bangumi_id=bgm_id,
                output_dir=str(season_dir),
                tvdb_season_id=tvdb_id,
                overwrite=overwrite_assets,
            )
            nfo_count += int(bool(written) and (overwrite_assets or not season_nfo_exists))
            # Season poster from BGM images if available
            bgm_subject_images = None
            for e in bgm_cache.get(bgm_id, []):
                if isinstance(e, dict) and "images" in e:
                    bgm_subject_images = e.get("images")
                    break
            if download_images and bgm_subject_images:
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
        if download_thumbnails and still:
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
            "tmdb_series_id": tmdb_id,
            "has_thumb": bool(still),
            "thumb_metadata_complete": all(candidates.get(provider) for provider in ("tmdb", "tvdb")
                                           if mapping[provider]["series_id"] is not None),
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
        if not download_thumbnails:
            existing_thumb = next((Path(rec["season_dir"]) / f"{rec['file_stem']}-thumb{suffix}"
                                   for suffix in (".jpg", ".png", ".webp", ".jpeg", ".gif")
                                   if (Path(rec["season_dir"]) / f"{rec['file_stem']}-thumb{suffix}").is_file()), None)
            thumb_path = str(existing_thumb) if existing_thumb else ""
        if thumbnail_results is not None:
            if thumb_path:
                status, message = "success", "单集缩略图已生成"
            elif download_thumbnails and rec["has_thumb"]:
                status, message = "failed", "单集缩略图下载失败"
            elif not rec["thumb_metadata_complete"]:
                status, message = "failed", "部分平台剧集元数据不可用，无法确认缩略图是否缺失"
            else:
                status, message = "missing", "新映射没有可用的单集缩略图"
            thumbnail_results.append({"status": status, "path": thumb_path, "message": message})
            if status == "failed":
                continue
        episode_nfo_exists = (Path(rec["season_dir"]) / f"{rec['file_stem']}.nfo").exists()
        if overwrite or not episode_nfo_exists:
            logger.info("NFO [%s] 字段来源：标题=%s；简介=%s；"
                        "原名=Bangumi→标题；日期/时长=TMDB；评分=TVDB→TMDB；"
                        "季集编号=TVDB→TMDB；缩略图=%s",
                        rec["context"], rec["title_source"], rec["plot_source"],
                        "已下载" if thumb_path else "无")
        written = write_file("episode", Path(rec["season_dir"]) / f"{rec['file_stem']}.nfo",
            write_resolved_episode_nfo, episode=rec["resolved"],
            show_name=rec["show"].get("title") or (series_name or rec["bgm_subject_name"] if refresh_results is not None else str(rec["tmdb_series_id"])),
            bangumi_subject_name=rec["bgm_subject_name"],
            thumb_path=Path(thumb_path).name if thumb_path else "",
            output_dir=rec["season_dir"], file_stem=rec["file_stem"], overwrite=overwrite,
            preserve_file_stem=refresh_results is not None,
        )
        nfo_count += int(bool(written) and (overwrite or not episode_nfo_exists))

    # ── Phase 4: Clear caches, return summary ──
    bgm_cache.clear()
    bgm_subject_data_cache.clear()
    tvdb_cache.clear()
    tmdb_show_cache.clear()

    return {"nfoGenerated": nfo_count, "episodesProcessed": len(pending_eps), "imagesDownloaded": img_count,
            "episodePaths": [str((Path(rec["season_dir"]) / rec["file_stem"]).relative_to(pre_path)) for rec in pending_eps]}
