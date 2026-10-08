"""Deterministic resource decisions. Never fetch episodes or consume raw providers."""
import logging
import re
import unicodedata
from ..domain.resource import ResourceCandidate, ResourceIdentity, ResourceResolution, resource_identity, validate_resource_identity

logger = logging.getLogger(__name__)


def normalized_title(title: str | None) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", title or "").casefold())


class ResourceResolver:
    def resolve(self, candidates: list[ResourceCandidate], *, known: ResourceIdentity | None = None,
                existing_mapping: ResourceIdentity | None = None, title=None, year=None,
                media_type="tv", allow_single_fallback=True) -> ResourceResolution:
        def result(status, identity, reason):
            logger.info("resource.resolution status=%s reason=%s title=%r", status, reason, title)
            return dict(status=status, identity=identity, candidates=candidates, reason=reason)
        for identity, reason in ((known, "explicit_provider_identity"), (existing_mapping, "existing_mapping")):
            if identity is not None:
                validate_resource_identity(identity)
                return result("resolved", dict(identity), reason)
        compatible = [c for c in candidates if c["media_type"] in (media_type, "unknown")
                      or media_type == "tv" and c["media_type"] == "special"]
        # Same provider ID may occur in multiple query/alias responses.
        unique = {}
        for c in compatible:
            unique.setdefault((c["provider"], c["provider_id"]), c)
        selected = list(unique.values())
        reason = "legacy_single_candidate_fallback"
        if year is not None:
            matches = [c for c in selected if c["year"] == int(year)]
            if matches:
                selected = matches
                reason = "year"
        query = normalized_title(title)
        matches = [c for c in selected if query and query in {
            normalized_title(n) for n in [c["title"], c["original_title"], *c["alternative_titles"]]}]
        if matches:
            selected = matches
            reason = "exact_normalized_title"
        if not selected:
            return result("unresolved", None, "no_candidates")
        if len(selected) != 1:
            return result("ambiguous", None, "ambiguous_resource")
        if reason == "legacy_single_candidate_fallback" and not allow_single_fallback:
            return result("unresolved", None, "no_exact_candidate")
        candidate, = selected
        field = {"bangumi": "bangumi_subject_id", "tvdb": "tvdb_series_id",
                 "tmdb": "tmdb_movie_id" if media_type == "movie" else "tmdb_series_id"}[candidate["provider"]]
        if media_type == "movie" and candidate["provider"] == "tvdb":
            return result("unresolved", None, "unsupported_tvdb_movie_identity")
        identity = resource_identity(media_type, candidate["title"], **{field: candidate["provider_id"]})
        return result("resolved", identity, reason)


def select_provider_result(*args, **kwargs):
    from ..domain.resource_adapters import select_provider_result as project
    return project(*args, **kwargs)


def unique_relation(relations: list[dict], relation: str) -> dict | None:
    """A relationship does not assert equality. Branches require confirmation."""
    entries = {r["id"]: r for r in relations if r.get("relation") == relation}
    if len(entries) > 1:
        raise ValueError(f"ambiguous_resource: bangumi_relation:{relation}")
    return next(iter(entries.values()), None)


def resolve_primary_series_relation(relations: list[dict]) -> dict | None:
    # Only Bangumi's explicit 主线 is a main-series relation; side stories,
    # sequels, prequels and movies never imply the same provider binding.
    return unique_relation(relations, "主线")


def unique_provider_id(values) -> int | None:
    ids = {int(v) for v in values if v is not None and int(v) > 0}
    if len(ids) > 1:
        raise ValueError("ambiguous_resource: conflicting_provider_bindings")
    return next(iter(ids), None)


def resolve_special_binding(subject_id: int, main_identity: ResourceIdentity) -> ResourceResolution:
    """Explicit 主线 linkage keeps the special subject while binding the TV series.

    Caller must have confirmed the main relation and provider mapping. Season 0
    remains an episode rule and is intentionally absent from ResourceIdentity.
    """
    validate_resource_identity(main_identity)
    if main_identity["media_type"] != "tv":
        return dict(status="unresolved", identity=None, candidates=[], reason="special_main_is_not_tv")
    identity = dict(main_identity, bangumi_subject_id=subject_id)
    validate_resource_identity(identity)
    return dict(status="resolved", identity=identity, candidates=[], reason="bangumi_special_main_series_mapping")


def resolve_episode_link_scores(provider: str, scores: dict[int, float]) -> ResourceResolution:
    """Explicit compatibility rule for the existing RSS episode-name inference.

    The caller computes its existing fuzzy scores. A tie between resources is
    ambiguous; order, season and episode position do not break identity ties.
    """
    candidates = [dict(provider=provider, provider_id=pid, media_type="tv", title=None,
                       original_title=None, alternative_titles=[], year=None,
                       source="legacy_episode_name_link") for pid, score in scores.items() if score >= 0.6]
    if not candidates:
        return dict(status="unresolved", identity=None, candidates=[], reason="no_episode_name_link")
    best = max(scores[c["provider_id"]] for c in candidates)
    winners = [c for c in candidates if abs(scores[c["provider_id"]] - best) < 1e-9]
    result = ResourceResolver().resolve(winners)
    result["candidates"] = candidates
    if result["status"] == "resolved":
        result["reason"] = "legacy_episode_name_link"
    return result
