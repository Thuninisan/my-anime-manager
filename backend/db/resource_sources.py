"""Persistent RSS source configuration."""

from sqlalchemy import select
import re

from .connection import new_session
from .models import ResourceSource


def list_sources() -> dict[str, dict]:
    from ..services.resource_monitor.feeds import SOURCES
    with new_session() as session, session.begin():
        for slug, config in SOURCES.items():
            if session.get(ResourceSource, slug) is None:
                session.add(ResourceSource(name=slug, rss_url=config["rss_url"],
                                           downloadtag=config["downloadtag"],
                                           index_type=config["index_type"]))
        session.flush()
        return {source.name: {"name": source.name, "rss_url": source.rss_url,
                              "downloadtag": source.downloadtag, "index_type": source.index_type}
                for source in session.scalars(select(ResourceSource))}


def save_source(name: str, rss_url: str, downloadtag: dict, index_type: str) -> dict:
    if index_type not in {"tmdb", "tvdb"}:
        raise ValueError("index_type 必须是 tmdb 或 tvdb")
    if not name.strip() or not rss_url.startswith("https://"):
        raise ValueError("来源名称和 HTTPS RSS URL 必填")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("来源名称只能包含英文字母、数字、下划线和连字符")
    if not downloadtag.get("tag") or not isinstance(downloadtag.get("tag"), str):
        raise ValueError("downloadtag.tag 必填")
    with new_session() as session, session.begin():
        source = session.get(ResourceSource, name)
        if source is None:
            source = ResourceSource(name=name)
            session.add(source)
        source.rss_url = rss_url
        source.downloadtag = downloadtag
        source.index_type = index_type
    return {"name": name, "rss_url": rss_url, "downloadtag": downloadtag,
            "index_type": index_type}
