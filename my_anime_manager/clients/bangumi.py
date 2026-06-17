"""Bangumi API client using httpx."""

import asyncio
from urllib.parse import quote

import httpx

from .. import config


def _get_client() -> httpx.AsyncClient:
    """Create a configured Bangumi httpx client."""
    proxy = None
    if config.PROXY_HOST:
        proxy = f"http://{config.PROXY_HOST}:{config.PROXY_PORT}"

    return httpx.AsyncClient(
        base_url="https://api.bgm.tv",
        headers={
            "User-Agent": config.BANGUMI_UA,
            "Accept": "application/json",
        },
        proxy=proxy,
        timeout=30.0,
    )


async def _delay() -> None:
    """Rate-limit delay for Bangumi API."""
    await asyncio.sleep(config.API_DELAY_MS / 1000.0)


async def search_subjects(keyword: str) -> list[dict]:
    """Search Bangumi subjects (v0 API with legacy fallback).

    Args:
        keyword: Search keyword

    Returns:
        List of subject dicts
    """
    await _delay()
    async with _get_client() as client:
        try:
            res = await client.post(
                "/v0/search/subjects",
                json={"keyword": keyword, "filter": {"type": [2]}},
                params={"limit": 20},
            )
            data = res.json()
            return data.get("data", [])
        except Exception:
            # v0 search failed, try legacy API
            try:
                res = await client.get(
                    f"/search/subject/{quote(keyword)}?type=2"
                )
                data = res.json()
                raw_list = data.get("list", [])
                return [
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "name_cn": item.get("name_cn", ""),
                        "date": item.get("air_date", ""),
                        "eps": item.get("eps_count", 0),
                        "type": item.get("type", 2),
                        "images": item.get("images"),
                    }
                    for item in raw_list
                ]
            except Exception as e2:
                print(f"   ❌ Bangumi 搜索完全失败: {e2}")
                return []


async def get_subject(subject_id: int) -> dict:
    """Get Bangumi subject details.

    Args:
        subject_id: Bangumi subject ID

    Returns:
        Subject data dict
    """
    await _delay()
    async with _get_client() as client:
        res = await client.get(f"/v0/subjects/{subject_id}")
        return res.json()


async def get_relations(subject_id: int) -> list[dict]:
    """Get subject relations (prequel/sequel etc.).

    Args:
        subject_id: Bangumi subject ID

    Returns:
        List of relation dicts
    """
    await _delay()
    async with _get_client() as client:
        try:
            res = await client.get(f"/v0/subjects/{subject_id}/subjects")
            return res.json()
        except Exception as e:
            print(f"   ⚠️ 获取关系失败 (id={subject_id}): {e}")
            return []


async def get_episode_total(subject_id: int) -> int:
    """Get total episode count for a subject (lightweight request).

    Args:
        subject_id: Bangumi subject ID

    Returns:
        Total episode count, or 0 on failure
    """
    await _delay()
    async with _get_client() as client:
        try:
            res = await client.get(
                "/v0/episodes",
                params={"subject_id": subject_id, "type": 0, "limit": 1},
            )
            data = res.json()
            return data.get("total", 0)
        except Exception:
            return 0


async def get_episodes(subject_id: int) -> list[dict]:
    """Get all main-story episodes for a subject (paginated).

    Args:
        subject_id: Bangumi subject ID

    Returns:
        Sorted list of episode dicts
    """
    await _delay()
    all_eps = []
    offset = 0
    limit = 100

    async with _get_client() as client:
        while True:
            res = await client.get(
                "/v0/episodes",
                params={
                    "subject_id": subject_id,
                    "type": 0,  # main story only
                    "limit": limit,
                    "offset": offset,
                },
            )
            data = res.json()
            eps = data.get("data", [])
            all_eps.extend(eps)

            if len(all_eps) >= (data.get("total") or 0):
                break
            if len(eps) < limit:
                break
            offset += limit
            await _delay()

    # Sort: prefer sort field, ep field as fallback
    all_eps.sort(key=lambda e: (e.get("sort") or e.get("ep") or 0))
    return all_eps
