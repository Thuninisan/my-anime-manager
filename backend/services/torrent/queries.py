"""Read Torrent collection cards without remote metadata requests."""

from ... import data
from ...db import torrents as torrent_store
from ..metadata import posters as rss_poster


async def list_collections() -> list[dict]:
    cards = []
    titles: dict[int, str] = {}
    for record in reversed(torrent_store.list_torrents()):
        tags = []
        if record.get("encoding_group") == "ktnbytes":
            tags.append("ktnbytes")
        codec = (record.get("video_codec") or "").lower()
        if codec in {"av1", "h265"}:
            tags.append("AV1" if codec == "av1" else "H.265")
        bangumi_ids = list(dict.fromkeys(
            bid for bid in record.get("bangumi_ids", [])
            if isinstance(bid, int) and not isinstance(bid, bool) and bid > 0
        ))
        for bangumi_id in bangumi_ids or [None]:
            if bangumi_id is not None and bangumi_id not in titles:
                titles[bangumi_id] = data.get_bangumi_name(bangumi_id) or ""
            cards.append({
                "card_key": f"{record['info_hash']}:{bangumi_id if bangumi_id is not None else 'unmatched'}",
                "bangumi_id": bangumi_id,
                "title_cn": titles.get(bangumi_id, ""),
                "poster_url": rss_poster.poster_url(bangumi_id) if bangumi_id is not None else "",
                "tags": tags,
                "info_hash": record["info_hash"],
            })
    return cards
