"""RSS rules end here: all downstream consumers receive EpisodeMapping."""
import logging
from ...domain.episode import EpisodeMapping
from ...domain.rss_mapping import build_episode_mapping
from ...domain.episode_adapters import episode_catalog
from ...domain.rss_episode import RssEpisodeRef

logger = logging.getLogger(__name__)


async def subscription_episode_mapping(ref: RssEpisodeRef, sub: dict, subject_id: int,
                                       metadata_ctx, *, sort=None, overrides=None, tvdb_episode_number=None) -> EpisodeMapping:
    """The poll context owns raw metadata and normalized catalogs until job completion."""
    from ...domain.resource_adapters import subscription_identity
    identity = subscription_identity(sub, subject_id)
    rules = {"bangumi_subject_id": identity["bangumi_subject_id"], "bangumi_episode_sort": sort}
    provider_catalogs = {"bangumi": {}, "tmdb": {}, "tvdb": {}}
    for provider in ("bangumi", "tmdb", "tvdb"):
        try:
            if provider == "bangumi":
                provider_catalogs[provider][str(subject_id)] = {"episodes": await metadata_ctx.get_bgm_episodes(subject_id)}
                continue
            settings = sub.get(provider, {})
            series_id = identity[f"{provider}_series_id"]
            season = settings.get("season")
            if season is None:
                season = sub.get("bgm", {}).get("season", 1)
            override = overrides or {}
            if override.get(f"{provider}_series_id") != series_id:
                override = {}
            rules[provider] = {"series_id": series_id,
                "season_number": (override["tmdb_season_number"] if override.get("tmdb_season_number") is not None else season) if provider == "tmdb" else season,
                "episode_number": override.get("tmdb_episode_number") if provider == "tmdb" else tvdb_episode_number,
                "episode_offset": settings.get("ep_offset") or 0}
            if series_id is not None:
                provider_catalogs[provider][str(series_id)] = (
                    await metadata_ctx.get_tmdb_season_map(series_id, "zh-CN") if provider == "tmdb"
                    else await metadata_ctx.get_tvdb_series(series_id)) or {}
        except Exception:
            logger.exception("RSS catalog acquisition failed: %s subject=%s", provider, subject_id)
    catalog = episode_catalog(provider_catalogs)
    metadata_ctx.rss_episode_catalogs[subject_id] = catalog
    return build_episode_mapping(ref, rules, catalog)
