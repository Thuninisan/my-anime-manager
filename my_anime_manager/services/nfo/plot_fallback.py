"""Episode plot resolution with a three-tier fallback chain.

Fallback order
  1. TMDB  — season detail in ``zh-CN`` (cached per season)
  2. TVDB  — flat episode list in ``zho`` (cached per series)
  3. Bangumi → DeepSeek  — Japanese ``desc`` field translated to Chinese

All caches are process-lifetime in-memory dicts.  API calls only happen
on the first miss for a given season / series / text.
"""

import logging

from ...clients import tmdb as tmdb_client
from ...clients import tvdb as tvdb_client
from ...services.enrich import _get_bangumi_episodes
from .translate import translate_ja_to_zh

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Caches (process lifetime)
# ═══════════════════════════════════════════════════════════════════════

# (tmdb_tv_id, season_number) → {episode_number: overview_str}
_tmdb_season_zh: dict[tuple[int, int], dict[int, str]] = {}

# tvdb_series_id → {(season_number, episode_number): overview_str}
_tvdb_series_zh: dict[int, dict[tuple[int, int], str]] = {}


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

    Returns ``""`` when every tier comes up empty.
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
# Tier helpers
# ═══════════════════════════════════════════════════════════════════════


async def _try_tmdb_zh(tv_id: int, season: int, ep_num: int) -> str:
    """Fetch TMDB season detail in zh-CN, cache the whole season."""
    cache_key = (tv_id, season)

    if cache_key not in _tmdb_season_zh:
        try:
            resp = await tmdb_client.get_season_detail(
                tv_id, season, language="zh-CN",
            )
            data = resp.json()
            _tmdb_season_zh[cache_key] = {
                ep.get("episode_number", 0): (ep.get("overview") or "")
                for ep in data.get("episodes", [])
            }
            logger.info(
                "TMDB zh-CN season cache populated: tv=%d S%d (%d episodes)",
                tv_id, season, len(_tmdb_season_zh[cache_key]),
            )
        except Exception:
            logger.warning(
                "TMDB zh-CN season fetch failed (tv=%d S%d)", tv_id, season,
            )
            _tmdb_season_zh[cache_key] = {}

    return _tmdb_season_zh[cache_key].get(ep_num, "").strip()


async def _try_tvdb_zh(series_id: int, season: int, ep_num: int) -> str:
    """Fetch TVDB episodes in Chinese (zho), cache the whole series."""
    if series_id not in _tvdb_series_zh:
        try:
            resp = await tvdb_client.get_series_episodes(
                series_id, language="zho",
            )
            payload = resp.json()
            # TVDB v4 wraps response in {"data": {...}, "status": "success"}
            data = payload.get("data", payload)
            episodes = data.get("episodes", [])
            _tvdb_series_zh[series_id] = {
                (ep.get("seasonNumber", 0), ep.get("number", 0)):
                    (ep.get("overview") or "")
                for ep in episodes
            }
            logger.info(
                "TVDB zho series cache populated: series=%d (%d episodes)",
                series_id, len(_tvdb_series_zh[series_id]),
            )
        except Exception:
            logger.warning(
                "TVDB zho series fetch failed (series=%d)", series_id,
            )
            _tvdb_series_zh[series_id] = {}

    return _tvdb_series_zh[series_id].get((season, ep_num), "").strip()


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
        Chinese plot text, or ``""`` on empty / failure.
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
