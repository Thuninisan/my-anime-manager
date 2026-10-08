"""RSS parser input, independent of subscription and provider coordinates."""
from typing_extensions import TypedDict


class RssEpisodeRef(TypedDict):
    rss_episode_number: int | None
    title: str
    release_title: str | None
    parsed_show_name: str | None


def rss_episode_ref(parsed: dict, title: str, release_title: str | None = None) -> RssEpisodeRef:
    value = parsed.get("episode_number")
    try:
        number = int(value) if value is not None else None
    except (ValueError, TypeError):
        number = None
    return {"rss_episode_number": number, "title": title,
            "release_title": release_title, "parsed_show_name": parsed.get("anime_title")}


def item_episode_ref(item: dict) -> RssEpisodeRef:
    """Older API/feed snapshots are adapted only at the input boundary."""
    if "rss_episode_ref" in item:
        return item["rss_episode_ref"]
    value = item.get("episode_number")
    return rss_episode_ref({"episode_number": value if value != 0 else None},
                           item.get("title", ""), item.get("guid"))
