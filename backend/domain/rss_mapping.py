"""RSS numbering rules operating only on explicit references and catalogs."""

from typing_extensions import TypedDict, NotRequired
from .episode import EpisodeCatalog, EpisodeMapping, create_episode_mapping
from .rss_episode import RssEpisodeRef


class RssProviderRule(TypedDict):
    series_id: int | None
    season_number: int | None
    episode_number: NotRequired[int | None]
    episode_offset: NotRequired[int]


class RssMappingContext(TypedDict):
    bangumi_subject_id: int
    bangumi_episode_sort: NotRequired[int | None]
    rss_offset: NotRequired[int | None]
    tmdb: NotRequired[RssProviderRule]
    tvdb: NotRequired[RssProviderRule]


def normalize_episode_number(ref: RssEpisodeRef, offset: int | None) -> int | None:
    """来源规则：Bangumi sort = RSS 集数 + feed offset；None 表示尚未确定。"""
    number = ref["rss_episode_number"]
    return number + offset if number is not None and offset is not None else None


def build_episode_mapping(ref: RssEpisodeRef, context: RssMappingContext,
                          catalog: EpisodeCatalog) -> EpisodeMapping:
    logical = context.get("bangumi_episode_sort")
    if logical is None:
        logical = normalize_episode_number(ref, context.get("rss_offset"))
    if logical is None:
        raise ValueError("RSS episode number/offset is unknown")
    subject_id = context["bangumi_subject_id"]
    entry = catalog["bangumi"].get(str(subject_id), {})
    bgm = next((e for e in entry.get("episodes", []) if e["episode_absolute"] == logical), None)
    bangumi = {"subject_id": subject_id, "episode_id": None,
               "episode_number": None, "episode_absolute": logical}
    if bgm is not None:
        bangumi.update({key: bgm[key] for key in bangumi})
    providers = {}
    for provider in ("tmdb", "tvdb"):
        rule = context.get(provider, {})
        series_id = rule.get("series_id")
        season = rule.get("season_number")
        number = rule.get("episode_number")
        if number is None and series_id is not None:
            # 平台规则：季内集数 = Bangumi sort + mapping ep_offset。
            # 未知时仅在本次运行回退为 0，不能把回退值持久化成已确认的偏移。
            number = logical + (rule.get("episode_offset") or 0)
        series = catalog[provider].get(str(series_id), {})
        seasons = series.get("seasons", {}) if provider == "tvdb" else series
        selected = seasons.get(str(season), {})
        episode = next((e for e in selected.get("episodes", []) if e["episode_number"] == number), None)
        providers[provider] = {"series_id": series_id,
            "episode_id": episode["episode_id"] if episode is not None else None,
            "season_number": season, "episode_number": number}
    return create_episode_mapping(
        {"season_number": None, "episode_number": logical}, bangumi,
        providers["tmdb"], providers["tvdb"],
    )
