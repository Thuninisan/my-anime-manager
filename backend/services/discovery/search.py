"""Project online anime search results for the discovery/subscribe workflow."""

import logging

import httpx

from ...clients import bangumi as bgm_client, tmdb, tvdb
from ...clients.errors import MissingAPIKeyError
from ..metadata import posters as rss_poster

logger = logging.getLogger(__name__)


class SearchUnavailable(RuntimeError):
    """The online search request failed before result projection."""


async def search_bangumi(q: str) -> list[dict]:
    if not q.strip():
        return []
    try:
        subjects = await bgm_client.search_subjects(q.strip())
    except Exception as exc:
        raise SearchUnavailable(f"Bangumi 搜索失败: {exc}") from exc
    results = []
    for item in subjects:
        if not item.get("id"):
            continue
        poster = (item.get("images") or {}).get("small") or ""
        has_poster = rss_poster.remember_search_poster(item["id"], poster)
        results.append({
            "bangumi_id": item["id"], "name": item.get("name_cn") or item.get("name") or str(item["id"]),
            "name_original": item.get("name") or "", "date": item.get("date") or "",
            "poster_url": f"/api/rss/bangumi-search/{item['id']}/poster" if has_poster else "",
        })
    return results


async def search_candidates(provider: str, media_type: str, q: str) -> list[dict]:
    """只搜索候选，完整目录在用户选中后加载。"""
    if provider not in ("tmdb", "tvdb") or media_type not in ("tv", "movie"):
        raise ValueError("不支持的搜索类型")
    if provider == "tvdb" and media_type != "tv":
        raise ValueError("TVDB 暂不支持电影候选")
    if not q.strip():
        return []
    label = provider.upper()
    try:
        if provider == "tmdb":
            response = await (tmdb.search_movie(q.strip(), language="zh-CN") if media_type == "movie"
                              else tmdb.search_tv(q.strip(), language="zh-CN"))
        else:
            response = await tvdb.search_series(q.strip())
        response.raise_for_status()
        payload = response.json()
        if provider == "tvdb" and payload.get("status") != "success":
            raise SearchUnavailable("TVDB 搜索失败，请检查凭据或稍后重试")
        rows = payload.get("results" if provider == "tmdb" else "data")
        if not isinstance(rows, list):
            raise SearchUnavailable(f"{label} 搜索返回了无效数据，请稍后重试")
        results = []
        seen = set()
        for item in rows:
            if not isinstance(item, dict) or provider == "tvdb" and item.get("type", "series") != "series":
                continue
            # TVDB 搜索的 id 可能带 series- 前缀，添加目录时必须使用数字 tvdb_id。
            raw_id = (item.get("tvdb_id") or item.get("id")) if provider == "tvdb" else item.get("id")
            if isinstance(raw_id, bool) or not str(raw_id).isdigit():
                continue
            provider_id = int(raw_id)
            if provider_id <= 0 or provider_id in seen:
                continue
            seen.add(provider_id)
            if provider == "tmdb":
                name = item.get("title" if media_type == "movie" else "name")
                original = item.get("original_title" if media_type == "movie" else "original_name")
                date = item.get("release_date" if media_type == "movie" else "first_air_date")
                poster = f"https://image.tmdb.org/t/p/w185{item['poster_path']}" if item.get("poster_path") else ""
            else:
                translations = item.get("translations") or {}
                name = translations.get("zho") if isinstance(translations, dict) else None
                name = name or item.get("name")
                original = item.get("name")
                date = item.get("first_air_time") or item.get("year")
                poster = item.get("thumbnail") or item.get("image_url")
            results.append({
                "provider": provider, "provider_id": provider_id, "media_type": media_type,
                "name": str(name or original or provider_id), "name_original": str(original or ""),
                "date": str(date or ""), "poster_url": str(poster or ""),
            })
            if len(results) >= 20:
                break
        return results
    except MissingAPIKeyError as exc:
        raise SearchUnavailable(str(exc)) from exc
    except SearchUnavailable:
        raise
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        message = "鉴权失败，请检查 API 凭据" if status in (401, 403) else f"上游返回 HTTP {status}，请稍后重试"
        raise SearchUnavailable(f"{label} 搜索失败：{message}") from exc
    except Exception as exc:
        logger.warning("%s 搜索失败 error_type=%s", label, type(exc).__name__)
        raise SearchUnavailable(f"{label} 搜索失败，请检查网络、API 凭据或稍后重试") from exc
