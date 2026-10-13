"""Fetch feeds, detail pages, and .torrent files into the resource store."""

import asyncio
import logging
import os
import tempfile
from urllib.parse import urlparse


from ...db import resources
from ...db.resource_sources import list_sources
from ...utils.http_retry import fetch_with_retry
from .details import extract_description
from .feeds import parse_feed

logger = logging.getLogger(__name__)
MAX_TORRENT_BYTES = 20 * 1024 * 1024
MAX_DETAIL_BYTES = 2 * 1024 * 1024


def _check_url(source: str, url: str, feed_host: str | None) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != feed_host or parsed.port is not None:
        raise ValueError(f"Unexpected {source} URL host or scheme")


async def collect_source(source: str) -> dict:
    """Collect one source; item failures do not discard the rest of the feed."""
    sources = await asyncio.to_thread(list_sources)
    if source not in sources:
        raise ValueError(f"Unknown resource source: {source}")
    feed_host = urlparse(sources[source]["rss_url"]).hostname
    response = await fetch_with_retry(sources[source]["rss_url"], label=f"{source} feed")
    items = await asyncio.to_thread(parse_feed, source, response.content, sources[source])
    stats = {"source": source, "seen": len(items), "complete": 0, "failed": 0}
    for item in items:
        record = None
        try:
            _check_url(source, item["detail_url"], feed_host)
            _check_url(source, item["torrent_url"], feed_host)
            record = await asyncio.to_thread(resources.upsert_feed_item, item)
            if record["status"] == "complete" and record["torrent_path"] \
                    and os.path.isfile(record["torrent_path"]):
                stats["complete"] += 1
                continue
            await _complete_record(record, feed_host)
            stats["complete"] += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stats["failed"] += 1
            logger.exception("Resource collection failed: source=%s guid=%s", source, item["source_id"])
            if record is not None:
                await asyncio.to_thread(resources.update_resource, record["id"],
                                        status="failed", error=str(exc)[:500])
    return stats


async def _complete_record(record: dict, feed_host: str | None) -> None:
    source = record["source"]
    resource_id = record["id"]
    if not record["detail_fetched"]:
        response = await fetch_with_retry(record["detail_url"], label=f"{source} detail")
        _check_url(source, str(response.url), feed_host)
        if len(response.content) > MAX_DETAIL_BYTES:
            raise ValueError("Detail page exceeds size limit")
        description = await asyncio.to_thread(extract_description, source, response.text)
        await asyncio.to_thread(resources.update_resource, resource_id,
                                detail_description=description, detail_fetched=1)

    torrent_path = resources.torrent_file_path(source, record["source_id"])
    if not torrent_path.is_file():
        response = await fetch_with_retry(record["torrent_url"], timeout=60, label=f"{source} torrent")
        _check_url(source, str(response.url), feed_host)
        payload = response.content
        if not payload or len(payload) > MAX_TORRENT_BYTES:
            raise ValueError("Torrent is empty or exceeds size limit")
        if not payload.startswith(b"d"):
            raise ValueError("Response is not a torrent file")
        await asyncio.to_thread(_write_torrent, torrent_path, payload)

    await asyncio.to_thread(resources.update_resource, resource_id,
                            torrent_path=str(torrent_path), status="complete", error="")


def _write_torrent(torrent_path, payload: bytes) -> None:
    torrent_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".resource-", suffix=".torrent", dir=torrent_path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, torrent_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
