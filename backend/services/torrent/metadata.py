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
    preview_data: dict | None,
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
    selected_resources = [f.get("resource_identity") for f in files if not f.get("is_subtitle") and f.get("resource_identity")]
    if selected_resources:
        is_movie = all(i["media_type"] == "movie" for i in selected_resources)
    else:
        is_movie = any(isinstance(entry, dict) and entry.get("media_type") == "movie" for entry in search_results.values())

    try:
        if is_movie:
            # ── Movie mode: extract metadata + generate movie.nfo ──
            selected_movie_ids = {f["resource_identity"]["tmdb_movie_id"] for f in files
                                  if f.get("resource_identity") and f["resource_identity"]["media_type"] == "movie"}
            movie_entries = [v for v in search_results.values() if isinstance(v, dict)
                             and v.get("media_type") == "movie"
                             and (not selected_movie_ids or (v.get("tmdb") or {}).get("id") in selected_movie_ids)]
            if len(movie_entries) != 1:
                raise ValueError("ambiguous_resource: movie_nfo_context")
            movie_entry, = movie_entries
            tmdb_info = movie_entry.get("tmdb", {})
            tmdb_id = tmdb_info.get("id", 0)
            from ..nfo.generator import sanitize_path_name
            tmdb_name = sanitize_path_name(tmdb_info.get("name", "Unknown"))
            bangumi_ids = movie_entry.get("bangumi_ids", [])
            from ..resource_resolver import unique_provider_id
            bangumi_id = (movie_entry.get("bangumi") or {}).get("id") or unique_provider_id(bangumi_ids) or 0
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
                from ...domain.episode_metadata_adapters import (
                    legacy_download_episode_mapping, mapping_to_legacy_batch_episode,
                )
                mapping = legacy_download_episode_mapping(f, preview_data)
                episode_entry = mapping_to_legacy_batch_episode(mapping)
                episode_entry["_legacy_episode_mapping"] = f.get("episode_mapping") is None
                nfo_episodes.append(episode_entry)
            if nfo_episodes:
                from ..nfo.generator import batch_nfo_generator
                from ..nfo.metadata_context import MetadataContext
                from ...domain.episode_metadata_adapters import seed_preview_metadata
                metadata_ctx = MetadataContext()
                snapshot = preview_data.get("canonical_snapshot")
                if snapshot is not None:
                    metadata_ctx.preview_snapshot = snapshot
                    # Even absent provider catalogs are authoritative for this session.
                    for mapping in (entry["episode_mapping"] for entry in nfo_episodes):
                        tid = mapping["tmdb"]["series_id"]
                        vid = mapping["tvdb"]["series_id"]
                        bid = mapping["bangumi"]["subject_id"]
                        metadata_ctx.tmdb_season_maps[(tid, "zh-CN")] = {}
                        metadata_ctx.tvdb_series[(vid, "jpn")] = {}
                        metadata_ctx.bgm_episodes[bid] = []
                else:
                    seed_preview_metadata(metadata_ctx, preview_data)
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
