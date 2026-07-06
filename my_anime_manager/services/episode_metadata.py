"""Shared utility — generate episode NFO + thumbnail from IDs alone.

Both the torrent batch flow and the RSS download flow prepare the same
four parameters, then call this function to handle TMDB fetching,
thumbnail download and NFO generation in one shot.
"""

import logging
from pathlib import Path

from .. import config
from ..clients.tmdb import get_season_detail as _tmdb_get_season
from ..clients.bangumi import get_episode as _bgm_get_episode, get_subject as _bgm_get_subject
from . import tmdb as tmdb_service
from .image_downloader import download_episode_thumb
from .nfo_generator import generate_episode_nfo

logger = logging.getLogger(__name__)


async def generate_episode_metadata(
    tmdb_id: int,
    tmdb_season: int,
    tmdb_episode: int,
    bangumi_episode_id: int | None = None,
    *,
    output_dir: str | None = None,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> dict:
    """Generate episode ``.nfo`` and download thumbnail.

    Fetches TMDB show / season detail and (optionally) Bangumi episode
    info internally — callers only need the four IDs.

    Args:
        tmdb_id:           TMDB TV show ID.
        tmdb_season:       TMDB season number (used to fetch season detail).
        tmdb_episode:      TMDB episode number (looked up inside season detail).
        bangumi_episode_id: Bangumi episode ID, written into the NFO.  When
            provided the function also fetches the Bangumi episode to obtain
            a proper sort number and subject name for file naming.
        output_dir:        Output directory.  Defaults to
            ``{QBITTORRENT_SAVE_PATH}/{show_name}/Season {season_number}``.
        season_number:     Season number written into the NFO.  Defaults to
            *tmdb_season*.
        episode_number:    Episode / sort number written into the NFO.
            Defaults to the Bangumi episode ``sort`` when available,
            otherwise *tmdb_episode*.

    Returns:
        ``{"nfo_path": str, "thumb_path": str, "tmdb_ep": dict | None}``.
    """
    # ── 1. TMDB show detail (name / original_name / studios) ─────────
    detail = await tmdb_service.get_tv_show_detail(tmdb_id)
    show_name = detail.get("name", "")
    original_name = detail.get("original_name") or show_name
    studios = [
        s.get("name", "") for s in (detail.get("networks") or [])
    ] or [
        s.get("name", "") for s in (detail.get("production_companies") or [])
    ]

    # ── 2. TMDB season detail → find matching episode ────────────────
    resp = await _tmdb_get_season(tmdb_id, tmdb_season)
    season_data = resp.json()

    tmdb_ep: dict | None = None
    for ep in (season_data.get("episodes") or []):
        if ep.get("episode_number") == tmdb_episode:
            directors = [
                c["name"] for c in ep.get("crew", [])
                if c.get("job") == "Director"
            ]
            writers = [
                c["name"] for c in ep.get("crew", [])
                if c.get("job") == "Writer"
            ]
            guest_stars = [
                {"name": gs["name"], "character": gs.get("character", "")}
                for gs in ep.get("guest_stars", [])
            ]
            tmdb_ep = {
                "name": ep.get("name", ""),
                "overview": ep.get("overview", ""),
                "air_date": ep.get("air_date", ""),
                "runtime": ep.get("runtime", 0),
                "tmdb_id": ep["id"],
                "still_path": ep.get("still_path", ""),
                "directors": directors,
                "writers": writers,
                "guest_stars": guest_stars,
            }
            break

    if not tmdb_ep:
        logger.warning(
            "TMDB S%d has no episode %d, skipping NFO",
            tmdb_season, tmdb_episode,
        )
        return {"nfo_path": "", "thumb_path": "", "tmdb_ep": None}

    # ── 3. Bangumi episode (sort + subject name for file naming) ─────
    bgm_sort: int | None = None
    bangumi_subject_name = ""
    bgm_subject_id: int | None = None

    if bangumi_episode_id:
        try:
            bgm_ep = await _bgm_get_episode(bangumi_episode_id)
            bgm_sort = bgm_ep.get("sort") or tmdb_episode
            bgm_subject_id = bgm_ep.get("subject_id")
            if bgm_subject_id:
                try:
                    subject = await _bgm_get_subject(bgm_subject_id)
                    bangumi_subject_name = (
                        subject.get("name_cn") or subject.get("name") or ""
                    ).strip()
                except Exception:
                    logger.warning(
                        "Bangumi subject %d lookup failed (non-fatal)",
                        bgm_subject_id,
                    )
        except Exception:
            logger.warning(
                "Bangumi episode %d lookup failed (non-fatal)",
                bangumi_episode_id,
            )

    # ── 4. Resolve NFO fields ────────────────────────────────────────
    se_num = season_number if season_number is not None else tmdb_season
    ep_num = (
        episode_number
        or bgm_sort
        or tmdb_episode
    )
    file_subject_name = bangumi_subject_name or show_name

    # ── 5. Output directory ──────────────────────────────────────────
    if output_dir is None:
        output_dir = str(
            Path(config.QBITTORRENT_SAVE_PATH)
            / show_name
            / f"Season {se_num}"
        )
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # ── 6. Thumbnail ─────────────────────────────────────────────────
    thumb_base = f"{file_subject_name} {ep_num:02d}"
    thumb_path = ""
    still = tmdb_ep.get("still_path", "")
    if still:
        try:
            thumb_path = await download_episode_thumb(
                still, output_dir, thumb_base,
            ) or ""
        except Exception:
            logger.exception("thumbnail download failed (non-fatal)")

    # ── 7. Episode NFO ───────────────────────────────────────────────
    nfo_path = generate_episode_nfo(
        tmdb_show_name=show_name,
        tmdb_original_name=original_name,
        tmdb_ep_name=tmdb_ep["name"],
        tmdb_ep_overview=tmdb_ep["overview"],
        tmdb_ep_air_date=tmdb_ep["air_date"],
        tmdb_ep_runtime=tmdb_ep["runtime"],
        tmdb_ep_id=tmdb_ep["tmdb_id"],
        season_number=se_num,
        episode_number=ep_num,
        bangumi_ep_id=bangumi_episode_id,
        bangumi_subject_name=file_subject_name,
        directors=tmdb_ep["directors"],
        writers=tmdb_ep["writers"],
        actors=tmdb_ep["guest_stars"],
        thumb_path=Path(thumb_path).name if thumb_path else "",
        studios=studios,
        output_dir=output_dir,
    )

    return {
        "nfo_path": nfo_path,
        "thumb_path": thumb_path,
        "tmdb_ep": tmdb_ep,
    }
