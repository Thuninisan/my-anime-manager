"""Deprecated external REST v1 DTOs; repository-owned frontend uses v2."""
from pydantic import model_validator
from .models import SubscriptionOut, TmdbMeta, TvdbMeta


class V1TmdbMeta(TmdbMeta):
    id: int = 0


class V1TvdbMeta(TvdbMeta):
    id: int = 0


class V1SubscriptionOut(SubscriptionOut):
    tmdb: V1TmdbMeta = V1TmdbMeta()
    tvdb: V1TvdbMeta = V1TvdbMeta()

    @model_validator(mode="before")
    @classmethod
    def project_provider_ids(cls, value):
        if not isinstance(value, dict) or not value.get("resource_identity"):
            return value
        identity = value["resource_identity"]
        tmdb_id = identity["tmdb_movie_id"] if identity["media_type"] == "movie" else identity["tmdb_series_id"]
        return {**value,
                "tmdb": {**value.get("tmdb", {}), "id": tmdb_id or 0},
                "tvdb": {**value.get("tvdb", {}), "id": identity["tvdb_series_id"] or 0}}


async def history_stream_v1(iterator):
    """Project only the deprecated public override names, without DB access."""
    import json
    async for chunk in iterator:
        event = json.loads(chunk)
        if event.get("type") == "data":
            event = {**event, "episodes": [{**entry,
                "tmdb_ep": entry.get("tmdb_episode_override"),
                "tmdb_season": entry.get("tmdb_season_override")}
                for entry in event.get("episodes", [])]}
        yield (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
