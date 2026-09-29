"""Episode / season plot resolution with a three-tier fallback chain.

Fallback order
  1. TMDB  — season detail in ``zh-CN``
  2. TVDB  — flat episode list in ``zho``
  3. Bangumi → DeepSeek  — Japanese ``desc`` field translated to Chinese

All tiers make a fresh API call on every invocation (no in-process caches).
"""

import logging

from ...clients import tmdb as tmdb_client
from ...clients import tvdb as tvdb_client
from ...services.enrich import _get_bangumi_episodes
from .translate import is_chinese_plot, translate_ja_to_zh

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════


async def resolve_episode_plot(
    *,
    tmdb_id: int = 0,
    tvdb_id: int = 0,
    tvdb_season: int = 0,
    tvdb_ep: int = 0,
    tmdb_season: int = 0,
    tmdb_ep_num: int = 0,
    bangumi_id: int = 0,
    bangumi_sort: int = 0,
    context: str = "episode.nfo",
    selected_source: list[str] | None = None,
) -> str:
    """Return the best available Chinese episode plot.

    All parameters are keyword-only.  A tier is skipped when its required
    IDs or episode number are missing; season zero is valid for specials.

    Returns ``""`` when no tier provides a Chinese plot.
    """
    foreign_plots: list[tuple[str, str]] = []

    def step(message: str) -> None:
        logger.info("NFO [%s 简介] %s", context, message)

    # ── Tier 1: TMDB zh-CN ─────────────────────────────────────────
    if tmdb_id and tmdb_season is not None and tmdb_ep_num:
        plot = await _try_tmdb_zh(tmdb_id, tmdb_season, tmdb_ep_num)
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
    if tvdb_id and tvdb_season is not None and tvdb_ep:
        plot = await _try_tvdb_zh(tvdb_id, tvdb_season, tvdb_ep)
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
    if bangumi_id and bangumi_sort:
        plot = await _try_bangumi_translate(bangumi_id, bangumi_sort)
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
# Season-level plot (Bangumi summary → Chinese)
# ═══════════════════════════════════════════════════════════════════════

BGM_SUMMARY_MARKER = "[简介原文]"


async def resolve_season_plot(
    bangumi_summary: str, *, context: str = "season.nfo",
    selected_source: list[str] | None = None,
) -> str:
    """Extract or translate a Bangumi subject summary for season.nfo.

    Bangumi summaries sometimes embed a Chinese translation prefixed
    with ``[简介原文]``.  When that marker is present the text before it
    is used directly (no API call).  Otherwise the summary is sent to
    DeepSeek for Japanese → Chinese translation.

    Args:
        bangumi_summary: Raw ``summary`` field from Bangumi subject API.

    Returns:
        Chinese plot text, or ``""`` on empty input.  On translation
        failure an empty string is returned.
    """
    text = bangumi_summary.strip()
    if not text:
        logger.info("NFO [%s 简介] Bangumi：无简介", context)
        return ""

    if BGM_SUMMARY_MARKER in text:
        chinese = text.split(BGM_SUMMARY_MARKER)[0].rstrip("\r\n")
        chinese = chinese.strip()
        if is_chinese_plot(chinese):
            if selected_source is not None:
                selected_source.append("Bangumi 内嵌中文")
            logger.info("NFO [%s 简介] Bangumi：命中内嵌中文简介", context)
            return chinese

    logger.info("NFO [%s 简介] Bangumi：尝试 DeepSeek 翻译", context)
    translated = await translate_ja_to_zh(text)
    if translated and selected_source is not None:
        selected_source.append("Bangumi → DeepSeek")
    logger.info("NFO [%s 简介] Bangumi → DeepSeek：%s", context,
                "翻译成功" if translated else "翻译失败，简介为空")
    return translated


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


async def _try_bangumi_translate(bangumi_id: int, sort: int) -> str:
    """Extract the Japanese ``desc`` from a cached Bangumi episode and
    translate it to Chinese via DeepSeek."""
    try:
        eps = await _get_bangumi_episodes(bangumi_id)
    except Exception:
        logger.warning(
            "Bangumi episode list fetch failed (id=%d)", bangumi_id,
        )
        return ""

    for ep in eps:
        if (ep.get("sort") or ep.get("ep", 0)) == sort:
            desc = (ep.get("desc") or "").strip()
            if desc:
                return await translate_ja_to_zh(desc)
            return ""

    logger.debug(
        "Bangumi episode not found: id=%d sort=%d", bangumi_id, sort,
    )
    return ""
