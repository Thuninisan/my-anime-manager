"""Validate resource feeds before saving their source configuration."""

from ...db.resource_sources import save_source
from ...utils.http_retry import fetch_with_retry
from .feeds import parse_feed


async def add_source(name: str, rss_url: str, downloadtag: dict, index_type: str) -> dict:
    if index_type not in {"tmdb", "tvdb"}:
        raise ValueError("index_type 必须是 tmdb 或 tvdb")
    if not rss_url.startswith("https://"):
        raise ValueError("RSS URL 必须使用 HTTPS")
    config = {"name": name, "rss_url": rss_url, "downloadtag": downloadtag, "index_type": index_type}
    response = await fetch_with_retry(rss_url, label=f"{name} feed")
    if not parse_feed(name, response.content, config):
        raise ValueError("Feed 中没有可验证的条目")
    return save_source(name, rss_url, downloadtag, index_type)
