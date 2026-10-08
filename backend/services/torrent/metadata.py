"""Pre-download NFO/metadata generation for the torrent flow.

Extracted from api/routes_torrent.py — generates movie.nfo or the TV
metadata collection from the frontend preview payload before the torrent
is resumed, so NFO files are ready when the download completes.
"""

import logging
from pathlib import Path

from ... import config

logger = logging.getLogger(__name__)

async def pre_generate_nfo(
    preview_snapshot: dict | None,
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
    if not preview_snapshot:
        return is_movie, nfo_generated, movie_meta

    series_contexts = preview_snapshot["series_contexts"]
    selected_resources = [f["resource_identity"] for f in files if not f.get("is_subtitle") and f.get("resource_identity")]
    is_movie = bool(selected_resources) and all(i["media_type"] == "movie" for i in selected_resources)

    try:
        if is_movie:
            # ── Movie mode: extract metadata + generate movie.nfo ──
            selected_movie_ids = {f["resource_identity"]["tmdb_movie_id"] for f in files
                                  if f.get("resource_identity") and f["resource_identity"]["media_type"] == "movie"}
            movie_entries = [v for v in series_contexts.values() if v["resource_identity"]
                             and v["resource_identity"]["media_type"] == "movie"
                             and v["resource_identity"]["tmdb_movie_id"] in selected_movie_ids]
            if len(movie_entries) != 1:
                raise ValueError("ambiguous_resource: movie_nfo_context")
            movie_entry, = movie_entries
            identity = movie_entry["resource_identity"]
            tmdb_id = identity["tmdb_movie_id"]
            from ..nfo.generator import sanitize_path_name
            tmdb_name = sanitize_path_name(movie_entry["display_name"])
            bangumi_id = identity["bangumi_subject_id"] or 0
            # Movie output path: {MOVIE_HARDLINK_PATH}/{tmdb_name}/
            movie_output_dir = Path(config.MOVIE_HARDLINK_PATH) / tmdb_name
            movie_output_dir.mkdir(parents=True, exist_ok=True)
            from ..nfo.nfo_xml import generate_movie_nfo
            nfo_path = generate_movie_nfo(
                tmdb_id=tmdb_id,
                bangumi_id=bangumi_id,
                output_dir=str(movie_output_dir),
            )
            nfo_generated = True
            movie_meta = {
                "tmdb_movie_id": tmdb_id,
                "tmdb_name": tmdb_name,
                "bangumi_subject_id": bangumi_id,
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
                nfo_episodes.append({"episode_mapping": f["episode_mapping"]})
            if nfo_episodes:
                from ..nfo.generator import batch_nfo_generator
                from ..nfo.metadata_context import MetadataContext
                metadata_ctx = MetadataContext()
                metadata_ctx.preview_snapshot = preview_snapshot
                for mapping in (entry["episode_mapping"] for entry in nfo_episodes):
                    metadata_ctx.tmdb_season_maps[(mapping["tmdb"]["series_id"], "zh-CN")] = {}
                    metadata_ctx.tvdb_series[(mapping["tvdb"]["series_id"], "jpn")] = {}
                    metadata_ctx.bgm_episodes[mapping["bangumi"]["subject_id"]] = []
                summary = await batch_nfo_generator(
                    hardlink_root, nfo_episodes, series_name=series_name, metadata_ctx=metadata_ctx,
                )
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
