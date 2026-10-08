"""Resolve RSS title clues to candidate Bangumi season entries."""

import logging
from collections.abc import Callable

from ... import config, data
from ...clients import bangumi, tmdb, tvdb
from ...db import resource_recognitions
from ...utils.episode_name_match import MIN_MATCH_SCORE, fuzzy_match_episode
from ..tvdb import fetch_tvdb_series_episodes
from .titles import parse_title

logger = logging.getLogger(__name__)


def missing_index_key(index_type: str) -> str | None:
    """Return the setting needed for recognition, if it is not configured."""
    setting = {"tvdb": "TVDB_API_KEY", "tmdb": "TMDB_API_KEY"}.get(index_type)
    if setting and not getattr(config, setting, "").strip():
        return setting
    return None


def compare_episode_names(index_episodes: list[dict] | None, bangumi_episodes: list[dict] | None,
                          number_key: str) -> tuple[str, int, str]:
    """Unknown data never means zero matches."""
    if index_episodes is None or bangumi_episodes is None:
        return "uncomparable", 0, "剧集接口失败"
    names = [ep.get("name", "") for ep in index_episodes if ep.get("name")]
    if not names:
        return "uncomparable", 0, "缺少可比较的集名"
    source_episode_names: list[list[str]] = []
    for ep in bangumi_episodes:
        source_names = [ep.get("name"), ep.get("name_cn")]
        source_names = [name for name in source_names if name]
        if source_names:
            source_episode_names.append(source_names)
    if not source_episode_names:
        return "uncomparable", 0, "缺少可比较的集名"
    # Count distinct episode pairs. Repeated or generic names cannot inflate
    # match_count beyond the number of episodes in the index season.
    edges = sorted(((max(fuzzy_match_episode(source, target) for source in source_names),
                     source_index, target_index)
                    for source_index, source_names in enumerate(source_episode_names)
                    for target_index, target in enumerate(names)), reverse=True)
    used_sources: set[int] = set()
    used_targets: set[int] = set()
    for score, source_index, target_index in edges:
        if score < MIN_MATCH_SCORE:
            break
        if source_index not in used_sources and target_index not in used_targets:
            used_sources.add(source_index)
            used_targets.add(target_index)
    matched = len(used_sources)
    return ("retained", matched, "至少一集名称匹配") if matched else (
        "excluded", 0, "可比较的集名全部不匹配")


async def recognize_resource(
    record: dict,
    cache: dict | None = None,
    *,
    persist: bool = True,
    on_step: Callable[[str, dict], None] | None = None,
) -> dict:
    """Recognize a resource; ``persist=False`` runs the exact flow read-only."""
    cache = cache if cache is not None else {}
    snapshot: dict = {"raw_title": record["title"], "index_type": record["index_type"]}
    candidates: list[dict] = []

    def emit(step: str, **details) -> None:
        if on_step is not None:
            on_step(step, details)

    def save(status: str, *, error: str = "") -> None:
        if persist:
            resource_recognitions.save(record["id"], snapshot, status,
                                       candidates if status == "complete" else [], error)

    try:
        snapshot = parse_title(record["title"], record["index_type"])
        emit("title", names=snapshot["name_candidates"], seasons=snapshot["seasons"],
             episode_range=snapshot["episode_range"], media_types=snapshot["media_types"])
        missing = missing_index_key(record["index_type"])
        if missing:
            message = f"缺少 {missing}；请在设置页面填写后重新识别"
            emit("missing_config", setting=missing, message=message)
            save("waiting_config", error=message)
            return {"title_snapshot": snapshot, "status": "waiting_config",
                    "error": message, "candidates": []}
        if snapshot["media_types"] == ["MOVIE"]:
            movie_results: list[dict] = []
            for name in snapshot["name_candidates"]:
                key = (record["index_type"], "movie_search", name)
                if key not in cache:
                    response = (await tmdb.search_movie(name, language="ja")
                                if record["index_type"] == "tmdb" else
                                await tvdb.search_movie(name))
                    payload = response.json()
                    raw = payload.get("results", []) if record["index_type"] == "tmdb" else payload.get("data", [])
                    cache[key] = raw if isinstance(raw, list) else raw.get("results", [])
                emit("search", index_type=record["index_type"], media_type="MOVIE",
                     query=name, result_count=len(cache[key]),
                     top_results=[{"id": item.get("tvdb_id") or item.get("id"),
                                   "name": item.get("name") or item.get("title")}
                                  for item in cache[key][:5]])
                movie_results.extend(cache[key])
                if movie_results:
                    break
            entries = data.get_movie_map_entries_by_titles(snapshot["name_candidates"])
            emit("mapping", index_type=record["index_type"], media_type="MOVIE", entries=entries)
            for entry in entries:
                mapped_id = int(entry.get(f"{record['index_type']}_id") or 0)
                search_ids = {int(item_id) for item in movie_results
                              if (item_id := item.get("tvdb_id") or item.get("id")) and str(item_id).isdigit()}
                confirmed = mapped_id in search_ids
                candidates.append({"bangumi_id": entry["bangumi_id"], "index_id": mapped_id,
                                   "index_season": 0, "media_type": "MOVIE",
                                   "decision": "retained" if confirmed else "uncomparable",
                                   "reason": ("电影索引 ID 与映射表一致" if confirmed else
                                              "标题别名与电影映射条目一致；索引 ID 尚未确认"),
                                   "match_count": 0})
            if not candidates:
                reason = "电影搜索与映射表都没有可确认的 Bangumi 条目"
                emit("unresolved", reason=reason)
                save("unresolved", error=reason)
                return {"title_snapshot": snapshot, "status": "unresolved",
                        "reason": reason, "candidates": []}
            emit("candidates", candidates=candidates)
            save("complete")
            return {"title_snapshot": snapshot, "status": "complete", "candidates": candidates}
        show = None
        mapped_candidates = []
        names = snapshot["name_candidates"]
        for name in names:
            key = (record["index_type"], "search", name)
            if key not in cache:
                if record["index_type"] == "tmdb":
                    response = await tmdb.search_tv(name, language="ja")
                    cache[key] = response.json().get("results", [])
                else:
                    response = await tvdb.search_series(name)
                    payload = response.json().get("data", [])
                    cache[key] = payload if isinstance(payload, list) else payload.get("results", [])
            emit("search", index_type=record["index_type"], query=name,
                 result_count=len(cache[key]),
                 top_results=[{"id": candidate.get("tvdb_id") or candidate.get("id"),
                               "name": candidate.get("name") or candidate.get("original_name")}
                              for candidate in cache[key][:5]])
            for candidate in cache[key]:
                candidate_id = (candidate.get("id") if record["index_type"] == "tmdb"
                                else candidate.get("tvdb_id") or candidate.get("id"))
                if not candidate_id:
                    continue
                try:
                    candidate_id = int(candidate_id)
                except (TypeError, ValueError):
                    continue
                mapped = (data.get_map_entries_by_tmdb_id(int(candidate_id))
                          if record["index_type"] == "tmdb" else
                          data.get_map_entries_by_tvdb_id(int(candidate_id)))
                if mapped:
                    mapped_candidates.append(candidate)
            if mapped_candidates:
                break  # Try another title alias only when this query has no mapped candidates.
        from ..resource_resolver import select_provider_result, ResourceResolver
        from ...domain.resource_adapters import provider_candidates
        # Mapping existence is a filter, not permission to select its first row.
        title = names[0] if names else None
        normalized = provider_candidates(record["index_type"], mapped_candidates, source="existing_mapping")
        resolution = ResourceResolver().resolve(normalized, title=title)
        if resolution["status"] == "ambiguous":
            emit("ambiguous", resolution=resolution)
            save("unresolved", error="ambiguous_resource")
            return {"title_snapshot": snapshot, "status": "unresolved", "reason": "ambiguous_resource",
                    "resource_resolution": resolution, "candidates": []}
        show = select_provider_result(record["index_type"], mapped_candidates, title, source="existing_mapping")
        if show is None:
            reason = "搜索结果中没有关联映射表的索引作品"
            emit("unresolved", reason=reason)
            save("unresolved", error=reason)
            return {"title_snapshot": snapshot, "status": "unresolved",
                    "reason": reason, "candidates": []}
        index_id = int(show["id"] if record["index_type"] == "tmdb" else show.get("tvdb_id") or show["id"])
        entries = (data.get_map_entries_by_tmdb_id(index_id) if record["index_type"] == "tmdb"
                   else data.get_map_entries_by_tvdb_id(index_id))
        emit("mapping", index_type=record["index_type"], index_id=index_id,
             show_name=show.get("name") or show.get("original_name"), entries=entries)
        for season in snapshot["seasons"]:
            explicit_entries = ([entry for entry in entries if entry.get("tmdb_season") == season]
                                if record["index_type"] == "tmdb" else [])
            key = (record["index_type"], "episodes", index_id, season)
            if key not in cache and (record["index_type"] == "tvdb" or snapshot["episode_range"]):
                try:
                    if record["index_type"] == "tmdb":
                        response = await tmdb.get_season_detail(index_id, season, language="ja")
                        cache[key] = response.json().get("episodes", [])
                    else:
                        series_key = ("tvdb", "series", index_id)
                        if series_key not in cache:
                            cache[series_key] = await fetch_tvdb_series_episodes(index_id)
                        series = cache[series_key]
                        cache[key] = (series or {}).get("seasons", {}).get(season, {}).get("episodes")
                except Exception:
                    logger.exception("Index episodes unavailable: id=%s season=%s", index_id, season)
                    cache[key] = None
            index_episodes = cache.get(key)
            if snapshot["episode_range"] and index_episodes is not None:
                low, high = snapshot["episode_range"]
                number_key = "episode_number" if record["index_type"] == "tmdb" else "epNum"
                index_episodes = [ep for ep in index_episodes if low <= int(ep.get(number_key) or 0) <= high]
            if record["index_type"] == "tvdb" or snapshot["episode_range"]:
                emit("index_episodes", season=season, available=index_episodes is not None,
                     count=len(index_episodes) if index_episodes is not None else None,
                     sample=[{"number": ep.get("episode_number") or ep.get("epNum"),
                              "name": ep.get("name")}
                             for ep in (index_episodes or [])[:5]])
            for entry in entries:
                if explicit_entries and entry not in explicit_entries:
                    continue
                mapped_season = entry.get(f"{record['index_type']}_season")
                if mapped_season is not None and int(mapped_season) != season:
                    continue
                bgm_id = entry["bangumi_id"]
                decision, count, reason = "retained", 0, "全季映射条目"
                if record["index_type"] == "tvdb" or snapshot["episode_range"]:
                    bgm_key = ("bangumi", bgm_id)
                    if bgm_key not in cache:
                        try:
                            cache[bgm_key] = await bangumi.get_episodes(bgm_id, ep_type=0)
                        except Exception:
                            logger.exception("Bangumi episodes unavailable: %s", bgm_id)
                            cache[bgm_key] = None
                    decision, count, reason = compare_episode_names(
                        index_episodes, cache[bgm_key],
                        "episode_number" if record["index_type"] == "tmdb" else "epNum")
                    emit("bangumi_comparison", bangumi_id=bgm_id, season=season,
                         episode_count=len(cache[bgm_key]) if cache[bgm_key] is not None else None,
                         sample=[{"sort": ep.get("sort"), "name": ep.get("name")}
                                 for ep in (cache[bgm_key] or [])[:5]],
                         decision=decision, match_count=count, reason=reason)
                candidates.append({"bangumi_id": bgm_id, "index_id": index_id,
                                   "index_season": season, "media_type": "TV",
                                   "decision": decision, "reason": reason, "match_count": count})
        if "MOVIE" in snapshot["media_types"]:
            movie_entries = [entry for entry in entries if entry.get("tvdb_season") == 0]
            if not movie_entries and names:
                for match in data.search_by_name(names[0]):
                    entry = data.get_map_entry(match["bangumi_id"]) or {}
                    if any(marker in entry.get("name", "").lower() for marker in
                           ("movie", "剧场版", "劇場版", "映画")):
                        movie_entries.append({"bangumi_id": match["bangumi_id"], **entry})
            for entry in movie_entries:
                if entry["bangumi_id"] in {candidate["bangumi_id"] for candidate in candidates}:
                    continue
                candidates.append({"bangumi_id": entry["bangumi_id"],
                                   "index_id": int(entry.get(f"{record['index_type']}_id") or index_id),
                                   "index_season": 0, "media_type": "MOVIE",
                                   "decision": "uncomparable", "reason": "标题含电影线索；需手动确认",
                                   "match_count": 0})
        emit("candidates", candidates=candidates)
        save("complete")
        return {"title_snapshot": snapshot, "status": "complete", "candidates": candidates,
                "resource_resolution": resolution, "resource_identity": resolution["identity"]}
    except Exception as exc:
        if persist:
            logger.exception("Resource recognition failed: id=%s", record["id"])
        emit("error", error=str(exc))
        save("failed", error=str(exc)[:500])
        return {"title_snapshot": snapshot, "status": "failed", "error": str(exc), "candidates": []}
