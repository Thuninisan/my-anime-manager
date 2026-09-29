"""Download and cache Bangumi covers used by the RSS interface."""

import asyncio
import logging
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from .. import config, data
from ..clients.bangumi import get_subject
from ..utils.http_retry import USER_AGENT

logger = logging.getLogger(__name__)
MAX_POSTER_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_locks: dict[int, asyncio.Lock] = {}
_search_posters: dict[int, list] = {}
SEARCH_POSTER_TTL = 600


def remember_search_poster(bangumi_id: int, url: str) -> bool:
    """Keep a search thumbnail in memory for ten minutes, without touching disk."""
    if not url or not _trusted_image_url(url):
        return False
    previous = _search_posters.get(bangumi_id)
    _search_posters[bangumi_id] = [url, previous[1], previous[2]] if previous and previous[0] == url else [url, None, ""]
    loop = asyncio.get_running_loop()
    entry = _search_posters[bangumi_id]
    loop.call_later(SEARCH_POSTER_TTL, lambda: _search_posters.pop(bangumi_id, None) if _search_posters.get(bangumi_id) is entry else None)
    return True


async def get_search_poster(bangumi_id: int) -> tuple[bytes, str] | None:
    entry = _search_posters.get(bangumi_id)
    if not entry:
        return None
    if entry[1] is not None:
        return entry[1], entry[2]
    try:
        proxy = f"http://{config.PROXY_HOST}:{config.PROXY_PORT}" if config.PROXY_HOST else None
        async with httpx.AsyncClient(proxy=proxy, timeout=15.0, headers={"User-Agent": USER_AGENT}) as client:
            async with client.stream("GET", entry[0]) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in IMAGE_TYPES:
                    return None
                chunks = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_POSTER_BYTES:
                        return None
                    chunks.append(chunk)
                if not size:
                    return None
                image = b"".join(chunks)
                if _search_posters.get(bangumi_id) is entry:
                    entry[1] = image
                    entry[2] = content_type
                return image, content_type
    except Exception as exc:
        logger.warning("Bangumi 搜索封面获取失败: id=%s error=%s", bangumi_id, type(exc).__name__)
        return None


def poster_url(bangumi_id: int) -> str:
    return f"/api/rss/bangumi/{bangumi_id}/poster"


def _cache_dir() -> Path:
    return data._USER_DATA_DIR / "rss_posters"


def _cached(bangumi_id: int) -> Path | None:
    directory = _cache_dir()
    for ext in IMAGE_TYPES.values():
        path = directory / f"{bangumi_id}{ext}"
        if path.is_file():
            return path
    return None


def _trusted_image_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and parsed.port in (None, 443) and (
        host in {"bgm.tv", "bangumi.tv"}
        or host.endswith(".bgm.tv")
        or host.endswith(".bangumi.tv")
    )


async def get_poster(bangumi_id: int) -> Path | None:
    """Return a cached cover or fetch one through the configured server proxy."""
    cached = _cached(bangumi_id)
    if cached:
        return cached
    lock = _locks.setdefault(bangumi_id, asyncio.Lock())
    async with lock:
        cached = _cached(bangumi_id)
        if cached:
            return cached
        try:
            subject = await get_subject(bangumi_id)
            images = subject.get("images") or {}
            url = next((images[key] for key in ("common", "medium", "large", "grid", "small") if images.get(key)), "")
            if not url or not _trusted_image_url(url):
                return None

            proxy = f"http://{config.PROXY_HOST}:{config.PROXY_PORT}" if config.PROXY_HOST else None
            async with httpx.AsyncClient(proxy=proxy, timeout=15.0, headers={"User-Agent": USER_AGENT}) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    ext = IMAGE_TYPES.get(content_type)
                    if not ext:
                        return None
                    directory = _cache_dir()
                    directory.mkdir(parents=True, exist_ok=True)
                    target = directory / f"{bangumi_id}{ext}"
                    temporary = directory / f".{bangumi_id}.{uuid4().hex}.tmp"
                    try:
                        size = 0
                        with temporary.open("wb") as output:
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > MAX_POSTER_BYTES:
                                    return None
                                output.write(chunk)
                        if not size:
                            return None
                        os.replace(temporary, target)
                        return target
                    finally:
                        temporary.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Bangumi RSS 封面获取失败: id=%s error=%s", bangumi_id, type(exc).__name__)
            return None
