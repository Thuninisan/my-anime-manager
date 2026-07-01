"""TMDB API client — all requests go through the shared retry wrapper."""

import httpx

from .. import config
from ..utils.http_retry import fetch_with_retry

TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/original"
_BASE = "https://api.themoviedb.org/3"

# Common query params for every TMDB API call.
_BASE_PARAMS = {"api_key": config.TMDB_API_KEY, "language": "ja"}


async def _tmdb_request(
    path: str,
    *,
    params: dict | None = None,
    label: str = "",
) -> httpx.Response:
    """Make a TMDB API request with automatic retry on transient errors."""
    merged = dict(_BASE_PARAMS)
    if params:
        merged.update(params)
    return await fetch_with_retry(
        f"{_BASE}{path}",
        params=merged,
        timeout=30.0,
        label=label,
    )


async def search_tv(query: str, language: str = "") -> httpx.Response:
    """Search for TV shows.  Pass *language* (e.g. ``"zh-CN"``) to override."""
    params: dict[str, str] = {"query": query}
    if language:
        params["language"] = language
    return await _tmdb_request(
        "/search/tv", params=params, label=f"TMDB search"
    )


async def get_tv_detail(tv_id: int) -> httpx.Response:
    """Get TV show details."""
    return await _tmdb_request(f"/tv/{tv_id}", label=f"TMDB tv/{tv_id}")


async def get_season_detail(tv_id: int, season_num: int, language: str = "") -> httpx.Response:
    """Get season details.  Pass *language* (e.g. ``"ja"``) for original titles."""
    params = {"language": language} if language else None
    return await _tmdb_request(
        f"/tv/{tv_id}/season/{season_num}",
        params=params,
        label=f"TMDB S{season_num}",
    )


async def get_episode_group_detail(group_id: str) -> httpx.Response:
    """Get episode group details."""
    return await _tmdb_request(
        f"/tv/episode_group/{group_id}",
        label=f"TMDB ep_group",
    )


async def get_alternative_titles(tv_id: int) -> httpx.Response:
    """Get all alternative titles for a TV show."""
    return await _tmdb_request(
        f"/tv/{tv_id}/alternative_titles",
        label=f"TMDB alt_titles",
    )


async def get_tv_images(tv_id: int, languages: str = "ja,zh,null") -> httpx.Response:
    """Get TV show images (backdrops, posters, logos)."""
    return await _tmdb_request(
        f"/tv/{tv_id}/images",
        params={"include_image_language": languages},
        label=f"TMDB images",
    )


# ── Movie endpoints ──

async def search_movie(query: str, language: str = "") -> httpx.Response:
    """Search for movies.  Pass *language* (e.g. ``"zh-CN"``) to override."""
    params: dict[str, str] = {"query": query}
    if language:
        params["language"] = language
    return await _tmdb_request(
        "/search/movie", params=params, label=f"TMDB movie search"
    )


async def get_movie_detail(movie_id: int) -> httpx.Response:
    """Get movie details (title, overview, release_date, runtime, etc.)."""
    return await _tmdb_request(f"/movie/{movie_id}", label=f"TMDB movie/{movie_id}")
