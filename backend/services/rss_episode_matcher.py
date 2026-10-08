"""RSS rules end here: all downstream consumers receive EpisodeMapping."""
import logging
from typing_extensions import TypedDict, NotRequired
from ..domain.episode import EpisodeCatalog, EpisodeMapping, create_episode_mapping
from ..domain.episode_adapters import episode_catalog
from ..domain.rss_episode import RssEpisodeRef

logger = logging.getLogger(__name__)


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
            number = logical + rule.get("episode_offset", 0)
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


async def subscription_episode_mapping(ref: RssEpisodeRef, sub: dict, subject_id: int,
                                       metadata_ctx, *, sort=None, overrides=None, tvdb_episode_number=None) -> EpisodeMapping:
    """The poll context owns raw metadata and normalized catalogs until job completion."""
    rules = {"bangumi_subject_id": subject_id, "bangumi_episode_sort": sort}
    legacy = {"bangumi": {}, "tmdb": {}, "tvdb": {}}
    for provider in ("bangumi", "tmdb", "tvdb"):
        try:
            if provider == "bangumi":
                legacy[provider][str(subject_id)] = {"episodes": await metadata_ctx.get_bgm_episodes(subject_id)}
                continue
            settings = sub.get(provider, {})
            series_id = settings.get("id") or None
            season = settings.get("season")
            if season is None:
                season = sub.get("bgm", {}).get("season", 1)
            override = overrides or {}
            rules[provider] = {"series_id": series_id,
                "season_number": (override["tmdb_season"] if override.get("tmdb_season") is not None else season) if provider == "tmdb" else season,
                "episode_number": override.get("tmdb_ep") if provider == "tmdb" else tvdb_episode_number,
                "episode_offset": settings.get("ep_offset", 0)}
            if series_id is not None:
                legacy[provider][str(series_id)] = (
                    await metadata_ctx.get_tmdb_season_map(series_id, "zh-CN") if provider == "tmdb"
                    else await metadata_ctx.get_tvdb_series(series_id)) or {}
        except Exception:
            logger.exception("RSS catalog acquisition failed: %s subject=%s", provider, subject_id)
    catalog = episode_catalog(legacy)
    metadata_ctx.rss_episode_catalogs[subject_id] = catalog
    return build_episode_mapping(ref, rules, catalog)
