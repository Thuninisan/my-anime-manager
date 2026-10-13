"""Bangumi broadcast calendar with one-hour cache and stale-data fallback."""

import logging
from time import monotonic

from ...clients import bangumi
from ..metadata import posters as rss_poster

logger = logging.getLogger(__name__)


class CalendarUnavailable(RuntimeError):
    """Calendar provider failed and no cached result is available."""


_cache: list[dict] | None = None
_cache_until = 0.0


async def get_calendar() -> list[dict]:
    global _cache, _cache_until
    if _cache is not None and monotonic() < _cache_until:
        return _cache

    try:
        calendar = await bangumi.get_calendar()
        if not isinstance(calendar, list):
            raise ValueError("Bangumi 日历格式无效")
        days = []
        for day in calendar:
            weekday = day.get("weekday") or {}
            weekday_id = weekday.get("id")
            if not isinstance(weekday_id, int) or not 1 <= weekday_id <= 7:
                continue
            items = []
            for subject in day.get("items") or []:
                if subject.get("type") != 2 or not subject.get("id"):
                    continue
                images = subject.get("images") or {}
                rating = subject.get("rating") or {}
                items.append({
                    "id": subject["id"],
                    "name": subject.get("name_cn") or subject.get("name") or str(subject["id"]),
                    "original_name": subject.get("name") or "",
                    # Fetch covers through the server's configured proxy and cache,
                    # just like RSS cards, instead of contacting Bangumi in the browser.
                    "poster_url": rss_poster.poster_url(subject["id"]) if any(
                        images.get(size) for size in ("common", "medium", "large", "grid", "small")
                    ) else "",
                    "rating": rating.get("score") or 0,
                    "air_date": subject.get("air_date") or "",
                })
            days.append({"weekday": weekday_id, "items": items})
        _cache = sorted(days, key=lambda day: day["weekday"])
        _cache_until = monotonic() + 3600
        return _cache
    except Exception as exc:
        logger.exception("获取 Bangumi 每日放送失败")
        if _cache is not None:
            return _cache
        raise CalendarUnavailable("无法获取 Bangumi 每日放送，请稍后重试") from exc
