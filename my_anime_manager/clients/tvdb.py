"""TVDB v4 API client — all requests go through the shared retry wrapper.

Authentication: POST /login with API key → JWT token (valid ~1 month).
Token is cached in memory and auto-refreshed on 401.
"""

import json
import logging

import httpx

from .. import config
from ..utils.http_retry import fetch_with_retry

logger = logging.getLogger(__name__)

_BASE = "https://api4.thetvdb.com/v4"

# Cached JWT token — cleared on 401 to trigger re-login.
_token: str | None = None


async def login() -> str:
    """Authenticate with the TVDB v4 API and return a JWT token.

    The token is cached at module level so subsequent calls reuse it
    without re-authenticating.
    """
    global _token
    if _token:
        return _token

    apikey = config.TVDB_API_KEY
    if not apikey:
        raise RuntimeError("TVDB_API_KEY is not configured")

    url = f"{_BASE}/login"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    body = json.dumps({"apikey": apikey})

    proxy_url = None
    if config.PROXY_HOST:
        proxy_url = f"http://{config.PROXY_HOST}:{config.PROXY_PORT}"

    async with httpx.AsyncClient(
        timeout=30.0,
        proxy=proxy_url,
    ) as client:
        resp = await client.post(url, content=body, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(
                f"TVDB login failed ({resp.status_code}): {resp.text[:500]}"
            )

    data = resp.json()
    _token = data.get("data", {}).get("token")
    if not _token:
        raise RuntimeError(f"TVDB login response missing token: {resp.text[:500]}")

    logger.info("TVDB login successful")
    return _token


def _clear_token() -> None:
    """Clear the cached token (called on 401 to force re-login)."""
    global _token
    _token = None


async def _ensure_auth() -> str:
    """Return a valid JWT token, logging in if necessary."""
    global _token
    if _token:
        return _token
    return await login()


async def _tvdb_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    label: str = "",
) -> httpx.Response:
    """Make a TVDB API request with automatic retry and 401 handling."""
    token = await _ensure_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        return await fetch_with_retry(
            f"{_BASE}{path}",
            method=method,
            headers=headers,
            params=params,
            timeout=30.0,
            label=label,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            # Token expired — re-login and retry once
            logger.info("TVDB token expired, re-authenticating...")
            _clear_token()
            token = await _ensure_auth()
            headers["Authorization"] = f"Bearer {token}"
            return await fetch_with_retry(
                f"{_BASE}{path}",
                method=method,
                headers=headers,
                params=params,
                timeout=30.0,
                label=f"{label} (retry)",
            )
        raise


# ═══════════════════════════════════════════════════════════════════════
# Series endpoints
# ═══════════════════════════════════════════════════════════════════════


async def get_series(series_id: int) -> httpx.Response:
    """Get base series information by TVDB series ID."""
    return await _tvdb_request(
        "GET", f"/series/{series_id}", label=f"TVDB series/{series_id}"
    )


async def get_series_extended(series_id: int) -> httpx.Response:
    """Get extended series information including seasons list."""
    return await _tvdb_request(
        "GET",
        f"/series/{series_id}/extended",
        label=f"TVDB series/{series_id}/extended",
    )


# ═══════════════════════════════════════════════════════════════════════
# Season endpoints
# ═══════════════════════════════════════════════════════════════════════


async def get_season_extended(season_id: int) -> httpx.Response:
    """Get extended season information including episodes list."""
    return await _tvdb_request(
        "GET",
        f"/seasons/{season_id}/extended",
        label=f"TVDB season/{season_id}/extended",
    )


# ═══════════════════════════════════════════════════════════════════════
# Search
# ═══════════════════════════════════════════════════════════════════════


async def search_series(query: str) -> httpx.Response:
    """Search for TV series by name."""
    return await _tvdb_request(
        "GET",
        "/search",
        params={"query": query, "type": "series"},
        label=f"TVDB search",
    )


# ═══════════════════════════════════════════════════════════════════════
# Episode endpoints
# ═══════════════════════════════════════════════════════════════════════


async def get_episode_extended(episode_id: int) -> httpx.Response:
    """Get extended episode information including credits, directors, writers."""
    return await _tvdb_request(
        "GET",
        f"/episodes/{episode_id}/extended",
        label=f"TVDB episode/{episode_id}/extended",
    )


# ═══════════════════════════════════════════════════════════════════════
# Translations
# ═══════════════════════════════════════════════════════════════════════


async def get_series_translations(series_id: int, language: str) -> httpx.Response:
    """Get translated series name and overview for a given language.

    Args:
        series_id: TVDB series ID.
        language: Three-letter language code (e.g. ``"jpn"``, ``"zho"``, ``"eng"``).
    """
    return await _tvdb_request(
        "GET",
        f"/series/{series_id}/translations/{language}",
        label=f"TVDB series/{series_id}/translations/{language}",
    )
