"""RSS/Atom entry normalization and per-entry torrent link extraction."""

import xml.etree.ElementTree as ET

SOURCES = {
    "ktnbytes": {"name": "KTNBytes", "rss_url": "https://ktnbytes.com/atom.xml",
                 "downloadtag": {"tag": "enclosure", "attribute": "url"}, "index_type": "tmdb"},
    "vcb-studio": {"name": "VCB-Studio", "rss_url": "https://nyaa.si/?page=rss&u=VCB-Studio",
                   "downloadtag": {"tag": "link", "attribute": None}, "index_type": "tvdb"},
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def extract_download_url(entry: ET.Element, downloadtag: dict) -> str:
    """Read only a direct child of this item/entry, never an adjacent entry."""
    tag, attribute = downloadtag.get("tag"), downloadtag.get("attribute")
    if not tag or not isinstance(tag, str):
        raise ValueError("downloadtag.tag 必须是 RSS 条目中的标签名称")
    for child in entry:
        if _local_name(child.tag) == tag:
            value = child.get(attribute, "") if attribute else (child.text or child.get("href", ""))
            value = value.strip()
            if not value:
                raise ValueError(f"条目的 {tag} 标签没有种子链接")
            if not (value.lower().split("?", 1)[0].endswith(".torrent") or
                    "/download/" in value.lower()):
                raise ValueError(f"{tag} 提取到的链接不是 .torrent 下载地址，可能是详情页: {value}")
            return value
    raise ValueError(f"RSS 条目缺少配置的种子标签: {tag}")


def parse_feed(source: str, content: bytes, config: dict | None = None) -> list[dict]:
    config = config or SOURCES.get(source)
    if config is None:
        raise ValueError(f"Unknown resource source: {source}")
    root = ET.fromstring(content)
    entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    items = []
    for entry in entries:
        values = {_local_name(child.tag): child for child in entry}

        def value(tag: str) -> str:
            node = values.get(tag)
            return (node.text or "").strip() if node is not None else ""

        title = value("title")
        guid = value("guid") or value("id")
        link_node = values.get("link")
        link = value("link") or (link_node.get("href", "") if link_node is not None else "")
        if not title:
            raise ValueError("RSS 条目缺少标题")
        torrent_url = extract_download_url(entry, config["downloadtag"])
        detail_url = link if source != "vcb-studio" else guid
        if not detail_url:
            detail_url = guid or link
        if not detail_url:
            raise ValueError("RSS 条目缺少详情页链接")
        items.append({"source": source, "source_id": guid or detail_url,
                      "index_type": config["index_type"], "title": title,
                      "published_at": value("pubDate") or value("updated") or value("published"),
                      "detail_url": detail_url, "torrent_url": torrent_url,
                      "rss_description": value("description") or value("summary"),
                      "info_hash": value("infoHash").lower(), "size_label": value("size")})
    return items
