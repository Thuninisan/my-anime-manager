"""TMDB API client using httpx."""

from urllib.parse import quote

import httpx

from .. import config

TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/original"


def _get_client() -> httpx.AsyncClient:
    """Create a configured TMDB httpx client."""
    proxy = None
    if config.PROXY_HOST:
        proxy = f"http://{config.PROXY_HOST}:{config.PROXY_PORT}"

    return httpx.AsyncClient(
        base_url="https://api.themoviedb.org/3",
        params={"api_key": config.TMDB_API_KEY, "language": "zh-CN"},
        proxy=proxy,
        timeout=30.0,
    )


async def search_tv(query: str) -> httpx.Response:
    """Search for TV shows.

    Args:
        query: Search keyword

    Returns:
        httpx Response object
    """
    async with _get_client() as client:
        return await client.get("/search/tv", params={"query": query})


async def get_tv_detail(tv_id: int) -> httpx.Response:
    """Get TV show details.

    Args:
        tv_id: TMDB show ID

    Returns:
        httpx Response object
    """
    async with _get_client() as client:
        return await client.get(f"/tv/{tv_id}")


async def get_season_detail(tv_id: int, season_num: int) -> httpx.Response:
    """Get season details.

    Args:
        tv_id: TMDB show ID
        season_num: Season number

    Returns:
        httpx Response object
    """
    async with _get_client() as client:
        return await client.get(f"/tv/{tv_id}/season/{season_num}")


async def get_episode_group_detail(group_id: str) -> httpx.Response:
    """Get episode group details.

    Args:
        group_id: Episode group ID

    Returns:
        httpx Response object
    """
    async with _get_client() as client:
        return await client.get(f"/tv/episode_group/{group_id}")


async def get_alternative_titles(tv_id: int) -> httpx.Response:
    """Get all alternative titles for a TV show.

    Args:
        tv_id: TMDB show ID

    Returns:
        httpx Response object with 'results' list of {iso_3166_1, title, type}
    """
    async with _get_client() as client:
        return await client.get(f"/tv/{tv_id}/alternative_titles")


async def get_tv_images(tv_id: int, languages: str = "ja,zh,null") -> httpx.Response:
    """Get TV show images (backdrops, posters, logos).

    Args:
        tv_id: TMDB show ID
        languages: Language preferences, e.g. 'ja,zh,null'

    Returns:
        httpx Response object
    """
    async with _get_client() as client:
        return await client.get(
            f"/tv/{tv_id}/images",
            params={"include_image_language": languages},
        )
