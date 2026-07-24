"""Episode file generation — thumbnail download + NFO writing.

Pure file-writing layer — no API calls.  Used by both the RSS flow
(:func:`~.metadata_builder.generate_metadata`) and the torrent batch
flow (:func:`~my_anime_manager.services.batch_service.generate_metadata_collection`).
"""

import logging
from pathlib import Path

from ... import config
from .images import download_episode_thumb
from .nfo_xml import generate_episode_nfo

logger = logging.getLogger(__name__)


def format_download_path(
    template: str, sub: dict, sort: int = 0, ext: str = "",
    bangumi_sort: int = 0, bangumi_ep: int = 0,
    tvdb_episode: int = 0, tmdb_episode: int = 0,
) -> str:
    """Format a download path template with subscription and episode variables.

    Uses Python ``str.format()`` syntax.  Available variables:

    ==================== ============================================
    ``{series_name}``    Root series name (from Bangumi chain)
    ``{bangumi_title}``  Current season Bangumi entry name
    ``{bgm_season}``     Bangumi chain position (int)
    ``{tvdb_season}``    TVDB season number (int)
    ``{tmdb_season}``    TMDB season number (int)
    ``{bangumi_sort}``   Bangumi sort number (sequential within entry)
    ``{bangumi_ep}``     Bangumi canonical episode number
    ``{tvdb_episode}``   Matched TVDB episode number
    ``{tmdb_episode}``   Matched TMDB episode number
    ``{sort}``           Alias for ``{bangumi_sort}`` (deprecated)
    ==================== ============================================

    Format specs are supported, e.g. ``{tvdb_episode:02d}`` → ``05``.
    The *ext* parameter (e.g. ``".mkv"``) is appended after formatting.
    """
    bgm = sub.get("bgm", {})
    tvdb = sub.get("tvdb", {})
    tmdb = sub.get("tmdb", {})
    return template.format(
        series_name=bgm.get("series_name") or sub.get("name", ""),
        bangumi_title=bgm.get("subject_name") or sub.get("name", ""),
        bgm_season=bgm.get("season", 1),
        tvdb_season=tvdb.get("season") or bgm.get("season", 1),
        tmdb_season=tmdb.get("season") or bgm.get("season", 1),
        sort=bangumi_sort or sort,
        bangumi_sort=bangumi_sort or sort,
        bangumi_ep=bangumi_ep or bangumi_sort or sort,
        tvdb_episode=tvdb_episode or bangumi_ep or bangumi_sort or sort,
        tmdb_episode=tmdb_episode or bangumi_sort or sort,
    ) + ext


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
) -> dict:
    """Download episode thumbnail and generate ``.nfo`` — no API calls.

    All metadata must already be extracted into *tmdb_ep* before
    calling.  Used by both the RSS flow (:func:`~.metadata_builder.generate_metadata`)
    and the torrent batch flow (:func:`~my_anime_manager.services.batch_service.generate_metadata_collection`).

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
    )

    return {"nfo_path": nfo_path, "thumb_path": thumb_path}
