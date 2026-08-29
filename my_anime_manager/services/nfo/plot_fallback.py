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
from .translate import translate_ja_to_zh

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
) -> str:
    """Return the best available Chinese episode plot.

    All parameters are keyword-only.  Each tier is skipped when its
    required IDs are zero / missing.

    Returns ``""`` when every tier comes up empty.  Tier 3 falls back
    to the original Japanese Bangumi ``desc`` when the DeepSeek
    translation fails entirely (a Japanese plot is better than none).
    """
    # ── Tier 1: TMDB zh-CN ─────────────────────────────────────────
    if tmdb_id and tmdb_season and tmdb_ep_num:
        plot = await _try_tmdb_zh(tmdb_id, tmdb_season, tmdb_ep_num)
        if plot:
            logger.debug("Episode plot resolved via TMDB zh-CN")
            return plot

    # ── Tier 2: TVDB Chinese ───────────────────────────────────────
    if tvdb_id and tvdb_season and tvdb_ep:
        plot = await _try_tvdb_zh(tvdb_id, tvdb_season, tvdb_ep)
        if plot:
            logger.debug("Episode plot resolved via TVDB zho")
            return plot

    # ── Tier 3: Bangumi → DeepSeek translate ───────────────────────
    if bangumi_id and bangumi_sort:
        plot = await _try_bangumi_translate(bangumi_id, bangumi_sort)
        if plot:
            logger.debug("Episode plot resolved via Bangumi + DeepSeek")
            return plot

    return ""


# ═══════════════════════════════════════════════════════════════════════
# Season-level plot (Bangumi summary → Chinese)
# ═══════════════════════════════════════════════════════════════════════

BGM_SUMMARY_MARKER = "[简介原文]"


async def resolve_season_plot(bangumi_summary: str) -> str:
    """Extract or translate a Bangumi subject summary for season.nfo.

    Bangumi summaries sometimes embed a Chinese translation prefixed
    with ``[简介原文]``.  When that marker is present the text before it
    is used directly (no API call).  Otherwise the summary is sent to
    DeepSeek for Japanese → Chinese translation.

    Args:
        bangumi_summary: Raw ``summary`` field from Bangumi subject API.

    Returns:
        Chinese plot text, or ``""`` on empty input.  On translation
        failure the original Japanese summary is returned as-is.
    """
    text = bangumi_summary.strip()
    if not text:
        return ""

    if BGM_SUMMARY_MARKER in text:
        chinese = text.split(BGM_SUMMARY_MARKER)[0].rstrip("\r\n")
        chinese = chinese.strip()
        if chinese:
            logger.debug("Season plot extracted from Bangumi inline translation")
            return chinese

    return await translate_ja_to_zh(text)


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
