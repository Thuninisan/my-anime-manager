"""Metadata orchestration — generate all NFO layers for a single episode download.

Called after a torrent is added to qBittorrent but before it is resumed.
Generates episode NFO + thumbnail, tvshow.nfo + images (if missing),
and season.nfo + poster (if missing), then renames the file in qBittorrent.
"""

import logging
from pathlib import Path

from ... import config
from ...clients.bangumi import get_subject
from ...clients.qbittorrent import rename_file
from ...data import get_all_episodes
from .. import tmdb as tmdb_service
from ..enrich import _get_bangumi_episodes, _get_bangumi_ep_id

from .generator import format_download_path, write_episode_files
from .images import download_season_poster, download_show_images
from .nfo_xml import generate_season_nfo, generate_tv_show_nfo

logger = logging.getLogger(__name__)


async def generate_metadata(
    qb_client, info_hash: str,
    bangumi_id: int, sort: int, bgm_subject_id: int,
    tmdb_id: int, show_name: str, old_torrent_path: str, guid: str,
    bgm_season: int = 1,
    tmdb_season: int | None = None,
    tmdb_ep_offset: int = 0,
    tvdb_id: int = 0,
    tvdb_season: int | None = None,
    tvdb_ep_offset: int = 0,
    tvdb_ep: int = 0,
    season_dir: str = "",
    show_dir: str = "",
    bgm_subject_name: str = "",
    series_name: str = "",
) -> bool:
    """Generate NFO files, download images, and rename in qBittorrent.

    *season_dir* is the directory for episode NFO / thumbnails (also
    used for season.nfo).  *show_dir* is its parent — used for
    tvshow.nfo and show-level images.  Both are derived from the
    download path template.
    """
    _season_dir = Path(season_dir)
    _show_dir = Path(show_dir)

    # ── Read override from download history ────────────────────────
    overrides = get_all_episodes(bangumi_id).get(str(sort), {})
    override_tmdb_ep = overrides.get("tmdb_ep")       # None if not set
    override_tmdb_season = overrides.get("tmdb_season")  # None if not set

    # ── TVDB: fetch episode data ───────────────────────────────────
    tvdb_ep_data = None
    tvdb_ep_id = 0
    ep_rating = 0.0
    # Saved for season.nfo reuse (season title, first episode air date)
    tvdb_season_info: dict | None = None
    tvdb_series_data: dict | None = None
    # Episode number variables for path template (initialised to sort as fallback)
    bangumi_ep_val = sort
    bgm_ep_name = ""
    bgm_ep_name_cn = ""
    tmdb_target_ep = 0
    if tvdb_id:
        try:
            from ...clients.tvdb import (
                get_series_extended as tvdb_get_series_extended,
                get_season_extended as tvdb_get_season_extended,
            )

            # Use Bangumi ``ep`` (canonical episode number) + split-season
            # offset for TVDB alignment (TVDB always starts at 1 per season).
            tvdb_ep_offset_val = tvdb_ep_offset or 0
            try:
                eps = await _get_bangumi_episodes(bangumi_id)
                for e in eps:
                    if (e.get("sort") or e.get("ep", 0)) == sort:
                        bangumi_ep_val = e.get("ep") or sort
                        bgm_ep_name = (e.get("name") or "").strip()
                        bgm_ep_name_cn = (e.get("name_cn") or "").strip()
                        break
            except Exception:
                pass
            if tvdb_ep < 1:
                tvdb_ep = 1

            target_tvdb_season = tvdb_season or 1
            logger.info(
                "fetching TVDB series=%d season=%d ep=%d (bgm_sort=%d, offset=%d)",
                tvdb_id, target_tvdb_season, tvdb_ep,
                bangumi_ep_val, tvdb_ep_offset_val,
            )

            # Step 1: Get series extended → find season ID
            series_resp = await tvdb_get_series_extended(tvdb_id)
            series_data = series_resp.json().get("data", series_resp.json())
            tvdb_series_data = series_data  # save for season.nfo
            season_id = None
            for s in series_data.get("seasons", []):
                if s.get("number") == target_tvdb_season:
                    season_id = s.get("id")
                    break
            if not season_id and series_data.get("seasons"):
                # Fallback: use first season
                season_id = series_data["seasons"][0].get("id")

            if season_id:
                # Step 2: Get season extended → find target episode
                season_resp = await tvdb_get_season_extended(season_id)
                season_info = season_resp.json().get("data", season_resp.json())
                tvdb_season_info = season_info  # save for season.nfo
                for ep in season_info.get("episodes", []):
                    if ep.get("number") == tvdb_ep:
                        tvdb_ep_id = ep.get("id")
                        tvdb_ep_data = {
                            "name": ep.get("name", ""),
                            "overview": ep.get("overview", ""),
                            "air_date": ep.get("airDate") or ep.get("aired", ""),
                            "runtime": ep.get("runtime", 0),
                            "still_path": ep.get("image", ""),
                            "tvdb_ep_id": tvdb_ep_id,
                        }
                        ep_rating = ep.get("siteRating", 0) or 0
                        break
        except Exception:
            logger.exception("TVDB episode fetch failed")

    if not tvdb_ep_data:
        logger.error("TVDB episode data not available for S%d E%d, skipping NFO",
                     tvdb_season or 1, tvdb_ep)
        return False

    tmdb_ep = tvdb_ep_data

    # ── Bangumi original Japanese name for <originaltitle> ─────────
    bgm_original_name = ""
    try:
        subject_for_name = await get_subject(bgm_subject_id)
        bgm_original_name = (subject_for_name.get("name") or "").strip()
    except Exception:
        logger.warning("Bangumi subject lookup for original name failed (non-fatal)")

    # ── Season directory ──────────────────────────────────────────
    _season_dir.mkdir(parents=True, exist_ok=True)

    # ── Episode thumb + NFO ───────────────────────────────────────
    # Override episode title from Bangumi (Chinese names)
    if bgm_ep_name_cn:
        tmdb_ep["name"] = bgm_ep_name_cn

    # Compute file stem from path template (same as media file)
    _tmpl = config.RSS_PATH_TEMPLATE
    _stem_sub = {
        "name": show_name,
        "bgm": {
            "series_name": series_name or show_name,
            "subject_name": bgm_subject_name or show_name,
            "season": bgm_season,
        },
        "tvdb": {"season": tvdb_season},
        "tmdb": {"season": tmdb_season},
    }
    _stem_path = format_download_path(
        _tmpl, _stem_sub, sort=sort, ext="",
        bangumi_sort=sort, bangumi_ep=int(bangumi_ep_val),
        tvdb_episode=tvdb_ep, tmdb_episode=tmdb_target_ep,
    )
    file_stem = Path(_stem_path).stem

    # Look up the Bangumi episode ID from the cached episode list
    bgm_ep_id = await _get_bangumi_ep_id(bangumi_id, sort)
    result = await write_episode_files(
        tmdb_ep,
        season_number=tvdb_season or bgm_season,
        episode_number=tvdb_ep,
        bangumi_ep_id=bgm_ep_id,
        show_name=show_name,
        original_name=bgm_ep_name or bgm_original_name or show_name,
        bangumi_subject_name=bgm_subject_name or show_name,
        rating=ep_rating,
        output_dir=str(_season_dir),
        thumb_source="tvdb" if tvdb_id and tvdb_ep_data else "tmdb",
        file_stem=file_stem,
    )
    logger.info("episode NFO: %s", result["nfo_path"])

    # ── Show-level NFO + images (only once) ───────────────────────
    tvshow_nfo = _show_dir / "tvshow.nfo"
    if not tvshow_nfo.exists() and tmdb_id:
        try:
            detail = await tmdb_service.get_tv_show_detail(tmdb_id)
            generate_tv_show_nfo(
                title=detail.get("name", show_name),
                original_title=detail.get("original_name", show_name),
                plot=detail.get("overview", ""),
                premiered=detail.get("first_air_date", ""),
                genres=detail.get("genres", []),
                studios=detail.get("studios", []),
                rating=detail.get("vote_average", 0),
                status=detail.get("status", ""),
                output_dir=str(_show_dir),
                tvdb_id=tvdb_id,
            )
            logger.info("tvshow.nfo generated")
            await download_show_images(tmdb_id, str(_show_dir))
        except Exception:
            logger.exception("tvshow.nfo failed")

    # ── Season NFO ────────────────────────────────────────────────
    season_nfo = _season_dir / "season.nfo"
    if not season_nfo.exists():
        try:
            season_title = show_name
            season_plot = ""
            season_premiered = ""

            if tvdb_season_info:
                # Premiered: first episode air date of this season
                eps = tvdb_season_info.get("episodes", [])
                if eps:
                    first_air = (eps[0].get("airDate") or eps[0].get("aired", ""))
                    if first_air:
                        season_premiered = first_air

            # Still download Bangumi poster
            subject = await get_subject(bgm_subject_id)
            season_title = (subject.get("name_cn") or subject.get("name") or show_name).strip()
            effective_season = tvdb_season or tmdb_season or bgm_season
            poster = await download_season_poster(subject, str(_show_dir), effective_season)
            if poster:
                logger.info("Season %d poster downloaded", effective_season)

            generate_season_nfo(
                title=season_title,
                original_title=subject.get("name", ""),
                plot=season_plot or subject.get("summary", ""),
                premiered=season_premiered or subject.get("date", ""),
                season_number=tvdb_season or bgm_season,
                bangumi_id=bgm_subject_id,
                output_dir=str(_season_dir),
            )
            logger.info("Season %d season.nfo generated", effective_season)
        except Exception:
            logger.exception("season.nfo failed")

    # ── Rename in qBittorrent ─────────────────────────────────────
    ext = Path(old_torrent_path).suffix
    new_path = format_download_path(
        _tmpl, _stem_sub, sort=sort, ext=ext,
        bangumi_sort=sort, bangumi_ep=int(bangumi_ep_val),
        tvdb_episode=tvdb_ep, tmdb_episode=tmdb_target_ep,
    )
    try:
        await rename_file(qb_client, info_hash, old_torrent_path, new_path)
        logger.info("renamed: %s → %s", old_torrent_path, new_path)
    except Exception:
        logger.exception("rename failed")

    return True
