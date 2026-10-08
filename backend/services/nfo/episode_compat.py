"""Temporary legacy NFO call boundary for RSS/batch/scan callers."""
import logging
from pathlib import Path
from .images import download_episode_thumb

from ...domain.episode import EpisodeMapping, create_episode_mapping
from ...domain.episode_metadata_adapters import provider_metadata_candidates
from ..episode_metadata_resolver import resolve_episode
from .nfo_xml import generate_episode_nfo as write_resolved_episode_nfo


logger = logging.getLogger(__name__)

def generate_episode_nfo(
    show_name: str, episode_name: str, plot: str, air_date: str,
    runtime: int | None, season_number: int, episode_number: int,
    bangumi_ep_id: int | None, original_name: str, bangumi_subject_name: str,
    directors: list[str] | None = None, writers: list[str] | None = None,
    actors: list[dict] | None = None, thumb_path: str = "",
    studios: list[str] | None = None, rating: float = 0.0,
    output_dir: str = ".", tvdb_ep_id: int | None = None,
    file_stem: str = "", overwrite: bool = False,
    episode_mapping: EpisodeMapping | None = None,
) -> str:
    mapping = episode_mapping if episode_mapping is not None else create_episode_mapping(
        {"season_number": None, "episode_number": None},
        {"subject_id": None, "episode_id": bangumi_ep_id,
         "episode_number": None, "episode_absolute": None},
        {"series_id": None, "episode_id": None,
         "season_number": season_number, "episode_number": episode_number},
        {"series_id": None, "episode_id": tvdb_ep_id,
         "season_number": None, "episode_number": None},
    )
    candidates = provider_metadata_candidates(tmdb={
        "name": episode_name, "overview": plot, "air_date": air_date,
        "runtime": runtime, "vote_average": rating, "directors": directors,
        "writers": writers, "guest_stars": actors,
    })
    resolved = resolve_episode(mapping, candidates)
    resolved["metadata"]["original_title"] = original_name
    resolved["provenance"]["original_title"] = "legacy"
    return write_resolved_episode_nfo(
        resolved, show_name=show_name, bangumi_subject_name=bangumi_subject_name,
        thumb_path=thumb_path, output_dir=output_dir, file_stem=file_stem,
        overwrite=overwrite,
    )


async def write_episode_files(
    tmdb_ep: dict,
    *,
    season_number: int,
    episode_number: int,
    bangumi_ep_id: int | None,
    show_name: str,
    original_name: str,
    bangumi_subject_name: str,
    studios: list[str] | None = None,
    rating: float = 0.0,
    output_dir: str = ".",
    thumb_source: str = "tmdb",
    file_stem: str = "",
    episode_mapping: EpisodeMapping | None = None,
) -> dict:
    """Download episode thumbnail and generate ``.nfo`` — no API calls.

    All metadata must already be extracted into *tmdb_ep* before
    calling.  Used by both the RSS flow (:func:`~.metadata_builder.generate_metadata`)
    and the torrent batch flow (:func:`~backend.services.torrent.batch_service.generate_metadata_collection`).

    Args:
        tmdb_ep: Normalised dict with keys ``name``, ``overview``,
            ``air_date``, ``runtime``, ``still_path``.
        season_number:  Season number written into the NFO.
        episode_number: Episode number written into the NFO.
        bangumi_ep_id:  Bangumi episode ID (or ``None``).
        show_name:      Show title → ``<showtitle>``.
        original_name:  Original name → ``<originaltitle>``.
        bangumi_subject_name: Bangumi subject name (fallback for file naming).
        studios:        Network / studio names.
        output_dir:     Directory to write NFO and thumbnail into.
        thumb_source:   ``"tvdb"`` or ``"tmdb"`` controls which CDN is used.
        file_stem:      Base filename (without extension) from path template.
            If empty, falls back to ``{bangumi_subject_name} {ep:02d}``.

    Returns:
        ``{"nfo_path": str, "thumb_path": str}``.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # ── Thumbnail ───────────────────────────────────────────────────
    if file_stem:
        thumb_base = file_stem
    else:
        thumb_base = f"{bangumi_subject_name or show_name} {episode_number:02d}"
    thumb_path = ""
    still = tmdb_ep.get("still_path", "")
    if still:
        try:
            if thumb_source == "tvdb":
                from .images import download_tvdb_episode_thumb
                thumb_path = await download_tvdb_episode_thumb(
                    still, output_dir, thumb_base,
                ) or ""
            else:
                thumb_path = await download_episode_thumb(
                    still, output_dir, thumb_base,
                ) or ""
        except Exception:
            logger.exception("thumbnail download failed (non-fatal)")

    # ── Episode NFO ─────────────────────────────────────────────────
    nfo_path = generate_episode_nfo(
        show_name=show_name,
        original_name=original_name,
        episode_name=tmdb_ep.get("name", ""),
        plot=tmdb_ep.get("overview", ""),
        air_date=tmdb_ep.get("air_date", ""),
        runtime=tmdb_ep.get("runtime", 0),
        season_number=season_number,
        episode_number=episode_number,
        bangumi_ep_id=bangumi_ep_id,
        bangumi_subject_name=bangumi_subject_name or show_name,
        directors=tmdb_ep.get("directors", []),
        writers=tmdb_ep.get("writers", []),
        actors=tmdb_ep.get("guest_stars", []),
        thumb_path=Path(thumb_path).name if thumb_path else "",
        studios=studios or [],
        rating=rating,
        output_dir=output_dir,
        tvdb_ep_id=tmdb_ep.get("tvdb_ep_id", 0),
        file_stem=file_stem,
        episode_mapping=episode_mapping,
    )

    return {"nfo_path": nfo_path, "thumb_path": thumb_path}


