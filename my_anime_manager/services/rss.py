"""RSS subscription service — Bangumi ID → Mikan subtitle groups, feed parsing."""

import xml.etree.ElementTree as ET

import httpx

from .. import config
from ..clients import mikan as mikan_client
from ..data import get_mikan_id, get_bangumi_name, get_rss_settings, is_downloaded
from ..vendor.anitopy import parse as anitopy_parse
from ..utils.http_retry import USER_AGENT


# ═══════════════════════════════════════════════════════════════════════
# Filter logic — tag-based matching
# ═══════════════════════════════════════════════════════════════════════

def _derive_tags(parsed: dict) -> list[str]:
    tags: list[str] = []
    lang_raw = parsed.get("language", "")
    lang_str = ", ".join(lang_raw) if isinstance(lang_raw, list) else str(lang_raw)
    if "简体中文" in lang_str: tags.append("简体")
    if "繁体中文" in lang_str: tags.append("繁体")
    if "日语" in lang_str or "日本語" in lang_str: tags.append("日语")
    subs_raw = parsed.get("subtitles", "")
    subs_str = subs_raw if isinstance(subs_raw, str) else ""
    if "内封" in subs_str: tags.append("内封")
    if "内嵌" in subs_str or "内挂" in subs_str: tags.append("内嵌")
    if "双语" in subs_str: tags.append("双语")
    # Resolution tags — anitopy extracts video_resolution from filenames
    # (e.g. "1080p", "1080", "720p", "720", "1920x1080")
    res = parsed.get("video_resolution", "")
    res_str = res if isinstance(res, str) else str(res)
    if "1080" in res_str:
        tags.append("1080p")
    elif "720" in res_str:
        tags.append("720p")
    return tags


def _extract_ep_num(parsed: dict) -> int:
    try:
        return int(parsed.get("episode_number", 0) or 0)
    except (ValueError, TypeError):
        return 0


def _matches_filter(item_tags: list[str], filter_tags: list[str] | None) -> bool:
    if not filter_tags:
        return True
    return all(t in item_tags for t in filter_tags)


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

async def lookup_bangumi_rss(bangumi_id: int) -> dict | None:
    mikan_id = get_mikan_id(bangumi_id)
    if mikan_id is None:
        return None
    name = get_bangumi_name(bangumi_id) or str(bangumi_id)
    base = config.MIKAN_BASE_URL
    groups = await mikan_client.get_subtitle_groups(mikan_id)
    return {
        "bangumi_id": bangumi_id, "name": name, "mikan_id": mikan_id,
        "global_rss": f"{base}/RSS/Bangumi?bangumiId={mikan_id}", "groups": groups,
    }


async def fetch_and_parse_rss(
    rss_url: str, filter_tags: list[str] | None = None, bangumi_id: int | None = None
) -> dict:
    proxy = None
    if config.PROXY_HOST:
        proxy = f"http://{config.PROXY_HOST}:{config.PROXY_PORT}"

    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(
        proxy=proxy, timeout=30.0, follow_redirects=True, headers=headers,
    ) as client:
        resp = await client.get(rss_url)
        resp.raise_for_status()

    root = ET.fromstring(resp.text)
    channel = root.find("channel")
    feed_title = channel.findtext("title", "") if channel is not None else ""

    ns = {"mikan": "https://mikanani.me/0.1/"}
    items: list[dict] = []
    settings = get_rss_settings()
    exclude_patterns = settings.get("exclude_patterns", [])

    for item_elem in root.iter("item"):
        guid_elem = item_elem.find("guid")
        guid = guid_elem.text.strip() if guid_elem is not None and guid_elem.text else ""
        title_elem = item_elem.find("title")
        title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
        enclosure = item_elem.find("enclosure")
        torrent_url = enclosure.get("url", "") if enclosure is not None else ""
        size_str = enclosure.get("length", "0") if enclosure is not None else "0"
        try: size_bytes = int(size_str)
        except (ValueError, TypeError): size_bytes = 0
        pub_date_elem = item_elem.find("pubDate")
        pub_date = pub_date_elem.text.strip() if pub_date_elem is not None and pub_date_elem.text else ""

        torrent_elem = item_elem.find("mikan:torrent", ns)
        if torrent_elem is not None:
            # Note: <mikan:link> is an episode *page* URL, not a .torrent file.
            # The real download URL is in <enclosure url="...">, so we don't
            # override torrent_url from mikan:link here.
            if torrent_elem.findtext("mikan:contentLength", "", ns) and size_bytes == 0:
                try:
                    size_bytes = int(torrent_elem.findtext("mikan:contentLength", "", ns))
                except ValueError:
                    pass
            if torrent_elem.findtext("mikan:pubDate", "", ns):
                pub_date = torrent_elem.findtext("mikan:pubDate", "", ns)

        text_to_parse = guid or title
        parsed = anitopy_parse(text_to_parse) or {}
        item_tags = _derive_tags(parsed)
        ep_num = _extract_ep_num(parsed)
        excluded = any(p in text_to_parse for p in exclude_patterns)

        # Episode-based dedup when bangumi_id available, else guid-based
        downloaded = is_downloaded(bangumi_id, ep_num) if bangumi_id and ep_num else False

        items.append({
            "guid": text_to_parse, "title": title,
            "torrent_url": torrent_url, "pub_date": pub_date,
            "size_bytes": size_bytes, "downloaded": downloaded,
            "tags": item_tags,
            "passed": _matches_filter(item_tags, filter_tags) and not excluded,
            "excluded": excluded,
            "episode_number": ep_num,
        })

    return {"title": feed_title, "items": items}
