"""Project online anime search results for the discovery/subscribe workflow."""

from ...clients import bangumi as bgm_client
from ..metadata import posters as rss_poster


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
