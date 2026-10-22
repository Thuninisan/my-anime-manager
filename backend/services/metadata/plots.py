"""集、季和作品的中文简介解析，按层级选择来源并共用翻译兜底。"""

import logging
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from ...clients import tmdb as tmdb_client
from ...clients import tvdb as tvdb_client
from .providers.bangumi import get_cached_episodes
from .translation import is_chinese_plot, translate_ja_to_zh, translate_synopsis_to_zh

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════


async def resolve_episode_plot(
    *,
    tmdb_id: int = 0,
    tvdb_id: int = 0,
    tvdb_season: int = 0,
    tvdb_episode_number: int = 0,
    tmdb_season: int = 0,
    tmdb_ep_num: int = 0,
    bangumi_id: int = 0,
    bangumi_sort: int = 0,
    bangumi_episode_id: int | None = None,
    context: str = "episode.nfo",
    selected_source: list[str] | None = None,
    metadata_ctx=None,
    episode_mapping=None,
) -> str:
    """Return the best available Chinese episode plot.

    All parameters are keyword-only.  A tier is skipped when its required
    IDs or episode number are missing; season zero is valid for specials.

    Returns ``""`` when no tier provides a Chinese plot.
    """
    foreign_plots: list[tuple[str, str]] = []
    preview_candidates = None
    if metadata_ctx is not None and metadata_ctx.preview_snapshot is not None and episode_mapping is not None:
        from ...domain.preview_metadata import metadata_candidates
        preview_candidates = metadata_candidates(metadata_ctx.preview_snapshot, episode_mapping)

    def step(message: str) -> None:
        logger.info("NFO [%s 简介] %s", context, message)

    # ── Tier 1: TMDB zh-CN ─────────────────────────────────────────
    if tmdb_id and tmdb_season is not None and tmdb_ep_num is not None:
        if metadata_ctx is None:
            plot = await _try_tmdb_zh(tmdb_id, tmdb_season, tmdb_ep_num)
        else:
            plot = ""
            try:
                season_map = await metadata_ctx.get_tmdb_season_map(tmdb_id, "zh-CN")
                if episode_mapping is not None:
                    from ...domain.episode_metadata_adapters import metadata_candidates_from_catalogs
                    candidate = metadata_candidates_from_catalogs(episode_mapping, season_map, {}, [])["tmdb"]
                    plot = (candidate["plot"] or "").strip() if candidate else ""
                else:
                    season_data = season_map.get(tmdb_season, season_map.get(str(tmdb_season), {}))
                    for episode in season_data.get("episodes", []):
                        if episode.get("epNum") == tmdb_ep_num:
                            plot = (episode.get("overview") or "").strip()
                            break
            except Exception:
                logger.warning("TMDB cached plot lookup failed", exc_info=True)
        if is_chinese_plot(plot):
            if selected_source is not None:
                selected_source.append("TMDB zh-CN")
            step("TMDB zh-CN：命中中文简介")
            return plot
        step("TMDB zh-CN：非中文" if plot else "TMDB zh-CN：无简介")
        if plot:
            foreign_plots.append(("TMDB", plot))
    else:
        step("TMDB zh-CN：缺少映射，跳过")

    # ── Tier 2: TVDB Chinese ───────────────────────────────────────
    if tvdb_id and tvdb_season is not None and tvdb_episode_number is not None:
        if preview_candidates is not None:
            plot = ""
            candidate = preview_candidates["tvdb"]
            if candidate is not None and candidate["provider_episode_id"] is not None:
                try:
                    translated = await metadata_ctx.get_tvdb_episode_translation(candidate["provider_episode_id"])
                    plot = (translated.get("overview") or "").strip()
                except Exception:
                    logger.warning("TVDB selected episode translation failed", exc_info=True)
        elif metadata_ctx is None:
            plot = await _try_tvdb_zh(tvdb_id, tvdb_season, tvdb_episode_number)
        else:
            plot = ""
            try:
                data = await metadata_ctx.get_tvdb_series(tvdb_id, "zho")
                for episode in data.get("episodes", []):
                    if episode.get("seasonNumber") == tvdb_season and episode.get("number") == tvdb_episode_number:
                        plot = (episode.get("overview") or "").strip()
                        break
            except Exception:
                logger.warning("TVDB cached plot lookup failed", exc_info=True)
        if is_chinese_plot(plot):
            if selected_source is not None:
                selected_source.append("TVDB zho")
            step("TVDB zho：命中中文简介")
            return plot
        step("TVDB zho：非中文" if plot else "TVDB zho：无简介")
        if plot:
            foreign_plots.append(("TVDB", plot))
    else:
        step("TVDB zho：缺少映射，跳过")

    # ── Tier 3: Bangumi → DeepSeek translate ───────────────────────
    if bangumi_id and (bangumi_sort is not None or bangumi_episode_id is not None):
        if preview_candidates is not None:
            candidate = preview_candidates["bangumi"]
            plot = await translate_ja_to_zh(candidate["plot"]) if candidate and candidate["plot"] else ""
        else:
            plot = await _try_bangumi_translate(bangumi_id, bangumi_sort, metadata_ctx=metadata_ctx, episode_id=bangumi_episode_id)
        if plot:
            if selected_source is not None:
                selected_source.append("Bangumi → DeepSeek")
            step("Bangumi → DeepSeek：翻译成功")
            return plot
        step("Bangumi → DeepSeek：无可用中文简介")
    else:
        step("Bangumi：缺少映射，跳过")

    for source_name, source in dict.fromkeys(foreign_plots):
        translated = await translate_ja_to_zh(source)
        if translated:
            if selected_source is not None:
                selected_source.append(f"{source_name} 原文 → DeepSeek")
            step(f"{source_name} 原文 → DeepSeek：翻译成功")
            return translated
        step(f"{source_name} 原文 → DeepSeek：翻译失败")
    step("最终无中文简介")
    return ""


# ═══════════════════════════════════════════════════════════════════════
# 季与作品简介：中文优先，缺失时翻译同一层级的原文
# ═══════════════════════════════════════════════════════════════════════

BGM_SUMMARY_MARKER = "[简介原文]"


@dataclass(frozen=True)
class PlotResolution:
    """季和作品简介的解析结果，失败不进入成功缓存。"""

    text: str = ""
    source: str | None = None
    status: str = "missing"


def retain_existing_plot(result: PlotResolution, path: Path) -> PlotResolution:
    """新简介不可用时保留已有的有效中文，避免覆盖刷新造成内容退化。"""
    if result.text or not path.is_file():
        return result
    try:
        text = (ElementTree.parse(path).getroot().findtext("plot") or "").strip()
    except (OSError, ElementTree.ParseError):
        logger.warning("NFO [%s 简介] 无法读取旧简介", path, exc_info=True)
        return result
    if is_chinese_plot(text):
        logger.warning("NFO [%s 简介] 新简介状态=%s，降级保留旧中文简介", path, result.status)
        return PlotResolution(text, "existing", "preserved")
    return result


async def _overview(loader, *, context: str, source: str) -> tuple[str, bool]:
    """单个来源失败时继续兜底，不影响下载流程。"""
    try:
        data = await loader()
        text = (data.get("overview") or "").strip()
        logger.info("NFO [%s 简介] %s：%s", context, source,
                    "命中中文" if is_chinese_plot(text) else "非中文" if text else "无简介")
        return text, False
    except Exception:
        logger.warning("NFO [%s 简介] %s 获取失败", context, source, exc_info=True)
        return "", True


def _finish_plot(result: PlotResolution, *, context: str, cache: dict, key: tuple) -> PlotResolution:
    if result.text:
        cache[key] = result
    logger.info("NFO [%s 简介] 最终来源=%s，状态=%s", context, result.source, result.status)
    return result


async def _translate_plots(
    candidates: list[tuple[str, str]], *, scope: str, context: str, seen: set[str],
) -> PlotResolution:
    """按候选优先级翻译，相同原文仅尝试一次。"""
    for source, text in candidates:
        if not text or text in seen:
            continue
        seen.add(text)
        logger.info("NFO [%s 简介] %s：尝试翻译", context, source)
        translated = await translate_synopsis_to_zh(text, scope=scope)
        logger.info("NFO [%s 简介] %s：%s", context, source, "翻译成功" if translated else "翻译失败")
        if translated:
            return PlotResolution(translated, f"{source}:translated", "translated")
    return PlotResolution(status="failed" if seen else "missing")


async def resolve_series_plot(
    *, tmdb_id: int = 0, tvdb_id: int = 0, metadata_ctx=None,
    context: str = "tvshow.nfo",
) -> PlotResolution:
    """优先使用同一作品的现成中文，再翻译作品原文，不借用某一季的简介。"""
    from .context import MetadataContext
    ctx = metadata_ctx or MetadataContext()
    key = ("series", tmdb_id, tvdb_id)
    if key in ctx.resolved_plots:
        return _finish_plot(ctx.resolved_plots[key], context=context, cache=ctx.resolved_plots, key=key)
    candidates = []
    attempted: set[str] = set()
    failed = False
    for source, enabled, loader in (
        ("tmdb", tmdb_id, lambda: ctx.get_tmdb_detail(tmdb_id, "zh-CN")),
        ("tvdb", tvdb_id, lambda: ctx.get_tvdb_series_translation(tvdb_id, "zho")),
    ):
        if not enabled:
            continue
        text, error = await _overview(loader, context=context, source=f"{source} 中文")
        failed |= error
        if is_chinese_plot(text):
            return _finish_plot(PlotResolution(text, source, "chinese"), context=context,
                                cache=ctx.resolved_plots, key=key)
        if text:
            candidates.append((source, text))

    # 先保留中文端点返回的原文，再按平台优先级补取日文和英文。
    for source, enabled, loaders in (
        ("tmdb", tmdb_id, [lambda lang=lang: ctx.get_tmdb_detail(tmdb_id, lang) for lang in ("ja", "en")]),
        ("tvdb", tvdb_id, [lambda lang=lang: ctx.get_tvdb_series_translation(tvdb_id, lang) for lang in ("jpn", "eng")]),
    ):
        originals = [(name, text) for name, text in candidates if name == source]
        if enabled and not originals:
            for loader in loaders:
                text, error = await _overview(loader, context=context, source=f"{source} 原文")
                failed |= error
                if text:
                    originals.append((source, text))
                    break
        result = await _translate_plots(originals, scope="series", context=context, seen=attempted)
        failed |= result.status == "failed"
        if result.text:
            return _finish_plot(result, context=context, cache=ctx.resolved_plots, key=key)
    return _finish_plot(PlotResolution(status="failed" if failed else "missing"),
                        context=context, cache=ctx.resolved_plots, key=key)


async def resolve_season_plot(
    bangumi_summary: str = "", *, tmdb_id: int = 0, tmdb_season: int | None = None,
    bangumi_id: int = 0, metadata_ctx=None, context: str = "season.nfo",
) -> PlotResolution:
    """只使用对应季与已映射 Bangumi 条目的简介，不使用作品简介代替。"""
    from .context import MetadataContext
    ctx = metadata_ctx or MetadataContext()
    key = ("season", tmdb_id, tmdb_season, bangumi_id, bangumi_summary)
    if key in ctx.resolved_plots:
        return _finish_plot(ctx.resolved_plots[key], context=context, cache=ctx.resolved_plots, key=key)
    foreign = ""
    attempted: set[str] = set()
    failed = False
    if tmdb_id and tmdb_season is not None:
        foreign, failed = await _overview(
            lambda: ctx.get_tmdb_season_detail(tmdb_id, tmdb_season, "zh-CN"),
            context=context, source="TMDB 季中文")
        if is_chinese_plot(foreign):
            return _finish_plot(PlotResolution(foreign, "tmdb", "chinese"), context=context,
                                cache=ctx.resolved_plots, key=key)
    text = (bangumi_summary or "").strip()
    if not text and bangumi_id:
        try:
            text = ((await ctx.get_bgm_subject(bangumi_id)).get("summary") or "").strip()
        except Exception:
            failed = True
            logger.warning("NFO [%s 简介] Bangumi summary 获取失败", context, exc_info=True)
    if BGM_SUMMARY_MARKER in text:
        chinese, original = text.split(BGM_SUMMARY_MARKER, 1)
        chinese = chinese.strip()
        if is_chinese_plot(chinese):
            return _finish_plot(PlotResolution(chinese, "bangumi", "chinese"), context=context,
                                cache=ctx.resolved_plots, key=key)
        text = original.strip() or chinese
    result = await _translate_plots([("bangumi", text)], scope="season", context=context, seen=attempted)
    failed |= result.status == "failed"
    if result.text:
        return _finish_plot(result, context=context, cache=ctx.resolved_plots, key=key)
    if tmdb_id and tmdb_season is not None:
        if not foreign:
            for language in ("ja", "en"):
                foreign, error = await _overview(
                    lambda: ctx.get_tmdb_season_detail(tmdb_id, tmdb_season, language),
                    context=context, source=f"TMDB 季 {language}")
                failed |= error
                if foreign:
                    break
        result = await _translate_plots([("tmdb", foreign)], scope="season", context=context, seen=attempted)
        failed |= result.status == "failed"
        if result.text:
            return _finish_plot(result, context=context, cache=ctx.resolved_plots, key=key)
    return _finish_plot(PlotResolution(status="failed" if failed else "missing"),
                        context=context, cache=ctx.resolved_plots, key=key)


# ═══════════════════════════════════════════════════════════════════════
# Tier helpers
# ═══════════════════════════════════════════════════════════════════════


async def _try_tmdb_zh(tv_id: int, season: int, ep_num: int) -> str:
    """Fetch TMDB season detail in zh-CN and extract the target episode overview."""
    try:
        resp = await tmdb_client.get_season_detail(
            tv_id, season, language="zh-CN",
        )
        data = resp.json()
        for ep in data.get("episodes", []):
            if ep.get("episode_number") == ep_num:
                return (ep.get("overview") or "").strip()
    except Exception:
        logger.warning(
            "TMDB zh-CN season fetch failed (tv=%d S%d)", tv_id, season,
        )
    return ""


async def _try_tvdb_zh(series_id: int, season: int, ep_num: int) -> str:
    """Fetch TVDB episodes in Chinese (zho) and extract the target episode overview."""
    try:
        resp = await tvdb_client.get_series_episodes(
            series_id, language="zho",
        )
        payload = resp.json()
        data = payload.get("data", payload)
        for ep in data.get("episodes", []):
            if (ep.get("seasonNumber") == season
                    and ep.get("number") == ep_num):
                return (ep.get("overview") or "").strip()
    except Exception:
        logger.warning(
            "TVDB zho series fetch failed (series=%d)", series_id,
        )
    return ""


async def _try_bangumi_translate(bangumi_id: int, sort: int, metadata_ctx=None, episode_id: int | None = None) -> str:
    """Extract the Japanese ``desc`` from a cached Bangumi episode and
    translate it to Chinese via DeepSeek."""
    try:
        eps = await (metadata_ctx.get_bgm_episodes(bangumi_id) if metadata_ctx else get_cached_episodes(bangumi_id))
    except Exception:
        logger.warning(
            "Bangumi episode list fetch failed (id=%d)", bangumi_id,
        )
        return ""

    for ep in eps:
        if (ep.get("id") == episode_id if episode_id is not None else ep.get("raw_sort", ep.get("sort")) == sort):
            desc = (ep.get("desc") or "").strip()
            if desc:
                return await translate_ja_to_zh(desc)
            return ""

    logger.debug(
        "Bangumi episode not found: id=%d sort=%d", bangumi_id, sort,
    )
    return ""
