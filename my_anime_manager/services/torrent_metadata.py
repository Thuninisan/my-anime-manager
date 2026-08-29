"""Pre-download NFO/metadata generation for the torrent flow.

Extracted from api/routes_torrent.py — generates movie.nfo or the TV
metadata collection from the frontend preview payload before the torrent
is resumed, so NFO files are ready when the download completes.
"""

import logging
from pathlib import Path

from .. import config

logger = logging.getLogger(__name__)

def _find_tmdb_id(preview_data: dict | None, show_name: str) -> int:
    """Find TMDB ID from preview_data for a given show name."""
    if not preview_data:
        return 0
    search_results = preview_data.get("search_results", {})
    for entry in search_results.values():
        tmdb = entry.get("tmdb", {}) if isinstance(entry, dict) else {}
        if tmdb.get("id"):
            return tmdb["id"]
    return 0


def _find_tvdb_id(preview_data: dict | None, bgm_id: int) -> int:
    """Find TVDB ID from preview_data's map_entries for a given BGM ID."""
    if not preview_data or not bgm_id:
        return 0
    search_results = preview_data.get("search_results", {})
    for entry in search_results.values():
        map_entries = entry.get("map_entries", []) if isinstance(entry, dict) else []
        for me in map_entries:
            if me.get("bangumi_id") == bgm_id and me.get("tvdb_id"):
                return me["tvdb_id"]
    return 0


async def pre_generate_nfo(
    preview_data: dict,
    files: list[dict],
    torrent_name: str,
    hardlink_root: str,
    series_name: str,
) -> tuple[bool, bool, dict | None]:
    """Generate NFO + images from the preview payload before resuming.

    Returns ``(is_movie, nfo_generated, movie_meta)``.  On failure the
    exception is logged and ``nfo_generated`` stays False so the caller
    falls back to inline NFO generation after the download completes.
    """
    is_movie = False
    movie_meta: dict | None = None
    nfo_generated = False
    if not preview_data:
        return is_movie, nfo_generated, movie_meta

    search_results = preview_data.get("search_results", {})
    for entry in search_results.values():
        if isinstance(entry, dict) and entry.get("media_type") == "movie":
            is_movie = True
            break

    try:
        if is_movie:
            # ── Movie mode: extract metadata + generate movie.nfo ──
            movie_entry = next(
                v for v in search_results.values()
                if isinstance(v, dict) and v.get("media_type") == "movie"
            )
            tmdb_info = movie_entry.get("tmdb", {})
            tmdb_id = tmdb_info.get("id", 0)
            from .nfo.generator import sanitize_path_name
            tmdb_name = sanitize_path_name(tmdb_info.get("name", "Unknown"))
            bangumi_ids = movie_entry.get("bangumi_ids", [])
            bangumi_id = bangumi_ids[0] if bangumi_ids else 0
            # Movie output path: {MOVIE_HARDLINK_PATH}/{tmdb_name}/
            movie_output_dir = Path(config.MOVIE_HARDLINK_PATH) / tmdb_name
            movie_output_dir.mkdir(parents=True, exist_ok=True)
            from .nfo.nfo_xml import generate_movie_nfo
            nfo_path = generate_movie_nfo(
                tmdb_id=tmdb_id,
                bangumi_id=bangumi_id,
                output_dir=str(movie_output_dir),
            )
            nfo_generated = True
            movie_meta = {
                "tmdb_id": tmdb_id,
                "tmdb_name": tmdb_name,
                "bangumi_id": bangumi_id,
            }
            logger.info(
                "预生成电影 NFO [%s]: %s (tmdb=%d, bangumi=%d)",
                torrent_name, nfo_path, tmdb_id, bangumi_id,
            )
        else:
            # Build episode list for batch_nfo_generator
            nfo_episodes: list[dict] = []
            for f in files:
                if f.get("is_subtitle"):
                    continue
                # Resolve bangumi_subject_id from the file's bangumi_id
                # (the search_result's bangumi.id for this show_name)
                bgm_id = f.get("bangumi_id", 0)
                nfo_episodes.append({
                    "bangumi_subject_id": bgm_id,
                    "bangumi_episode_sort": f.get("bangumi_sort", 0),
                    "tvdb_id": f.get("tvdb_season") and f.get("tvdb_episode") and _find_tvdb_id(preview_data, bgm_id) or 0,
                    "tvdb_season": f.get("tvdb_season"),     # None if not provided — 0 is valid (Specials)
                    "tvdb_episode": f.get("tvdb_episode"),   # None if not provided
                    "tmdb_id": _find_tmdb_id(preview_data, f.get("tmdb_show_name", "")),
                    "tmdb_season": f.get("tmdb_season", 0),
                    "tmdb_episode": f.get("tmdb_episode", 0),
                })
            if nfo_episodes:
                from .nfo.generator import batch_nfo_generator
                summary = await batch_nfo_generator(hardlink_root, nfo_episodes, series_name=series_name)
                nfo_generated = True
                logger.info(
                    "预生成元数据完成 [%s]: NFO=%d, images=%d",
                    torrent_name,
                    summary.get("nfoGenerated", 0),
                    summary.get("imagesDownloaded", 0),
                )
    except Exception as e:
        logger.warning("预生成元数据失败 [%s]: %s — 将在下载完成后重试", torrent_name, e)

    return is_movie, nfo_generated, movie_meta
