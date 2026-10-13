"""Torrent download monitor — poll qBittorrent, create hardlinks, copy subtitles.

File processing is invoked by ``torrent.recovery``, which owns the task
registry, checkpoint stages and cancellation. This module only waits for
downloads and executes media/subtitle file operations, independently of the API.
"""

import asyncio
import logging
from pathlib import Path

from ... import config
from ...domain.episode_metadata_adapters import episode_path_parameters
from ...clients.qbittorrent import get_torrents_by_hashes, login as qb_login
from ...utils.paths import SUBTITLE_DIR
from ..media.fonts import copy_subtitle
from ..media.planning import subtitle_suffix, sanitize_torrent_name, subscription_path_context
from ..media.linking import install_hardlink

logger = logging.getLogger(__name__)


async def monitor_processing(info_hash: str, torrent_name: str, processing: dict) -> set[Path] | None:
    """Resume a compact processing plan using qBittorrent's download path."""
    try:
        client = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
    except Exception:
        logger.exception("下载监控登录失败: %s", torrent_name)
        return None

    import time
    deadline = time.monotonic() + 86400
    while time.monotonic() < deadline:
        await asyncio.sleep(5)
        try:
            torrent = (await get_torrents_by_hashes(client, [info_hash])).get(info_hash)
        except Exception:
            logger.exception("下载监控轮询失败: %s", torrent_name)
            continue
        if not torrent or torrent.get("progress", 0) < 1.0:
            continue

        base = Path(torrent["save_path"])
        linked: set[Path] = set()
        for operation in processing["files"]:
            src = Path(operation["source_path"]) if "source_path" in operation else base / operation["torrent_path"]
            dest = Path(operation["target_path"])
            try:
                if not src.is_file():
                    logger.error("Torrent 源文件不存在: %s", src)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                if operation["action"] == "hardlink":
                    install_hardlink(src, dest)
                    linked.add(dest)
                else:
                    copy_subtitle(info_hash, src, dest)
            except OSError:
                logger.exception("Torrent 后处理失败: %s → %s", src, dest)
        return linked
    logger.warning("下载监控超时 [%s] (24h)", torrent_name)
    return None


async def monitor_download(
    info_hash: str,
    torrent_name: str,
    files: list[dict],
    uploaded_subtitles: list[dict],
    hardlink_root: str,
    series_name: str = "",
    *,
    skip_nfo: bool = False,
    movie_meta: dict | None = None,
) -> set[Path] | None:
    """Background task: poll qBittorrent until download completes, then
    create hardlinks / copy subtitles.

    When *skip_nfo* is True the inline NFO generation is skipped (it was
    already done before the torrent was resumed).
    """
    subtitle_dir = SUBTITLE_DIR / sanitize_torrent_name(torrent_name)

    # Login for the background task
    try:
        client = await qb_login(
            config.QBITTORRENT_URL,
            config.QBITTORRENT_USERNAME,
            config.QBITTORRENT_PASSWORD,
        )
    except Exception as e:
        logger.error("下载监控登录失败 [%s]: %s", torrent_name, e)
        return None

    import time
    deadline = time.monotonic() + 86400  # 24h max
    while time.monotonic() < deadline:
        await asyncio.sleep(5)
        try:
            torrents = await get_torrents_by_hashes(client, [info_hash])
        except Exception as e:
            logger.warning("下载监控轮询失败 [%s]: %s", torrent_name, e)
            continue

        t = torrents.get(info_hash)
        if not t:
            continue

        progress = t.get("progress", 0)
        state = t.get("state", "")

        if progress >= 1.0:
            logger.info("下载完成 [%s] (%.1f%%)", torrent_name, progress * 100)
            if progress < 1.0:
                logger.warning("种子状态异常 (progress=%.2f, state=%s), 仍然尝试创建文件", progress, state)

            save_path = t.get("save_path", hardlink_root)
            logger.info("下载完成 [%s], 开始创建硬链接/复制字幕...", torrent_name)

            created = 0
            linked_videos: set[Path] = set()

            if movie_meta:
                # ── Movie mode: flat structure {hardlink_root}/{tmdb_name}/{tmdb_name}.ext ──
                tmdb_name = movie_meta["tmdb_name"]
                movie_dir = Path(hardlink_root) / tmdb_name
                movie_dir.mkdir(parents=True, exist_ok=True)

                for f in files:
                    torrent_path = f["torrent_path"]
                    is_sub = f.get("is_subtitle", False)
                    src_ext = subtitle_suffix(f, Path(torrent_path).suffix) if is_sub else Path(torrent_path).suffix
                    src_path = Path(save_path) / torrent_path

                    if is_sub:
                        dest_path = movie_dir / f"{tmdb_name}{src_ext}"
                    else:
                        dest_path = movie_dir / f"{tmdb_name}{src_ext}"

                    try:
                        if src_path.exists():
                            if is_sub:
                                copy_subtitle(info_hash, src_path, dest_path)
                            else:
                                install_hardlink(src_path, dest_path)
                                linked_videos.add(dest_path)
                            created += 1
                            logger.info("   %s → %s [%s]", src_path.name, dest_path, "copy" if is_sub else "hardlink")
                        else:
                            logger.warning("   源文件不存在: %s", src_path)
                    except OSError as e:
                        logger.error("   创建文件失败: %s → %s — %s", src_path, dest_path, e)

                # Copy user-uploaded subtitles
                for usub in uploaded_subtitles:
                    stored_name = usub.get("stored_filename", "")
                    src_sub = subtitle_dir / stored_name
                    if not src_sub.exists():
                        logger.warning("   上传的字幕文件不存在: %s", src_sub)
                        continue
                    dest_path = movie_dir / f"{tmdb_name}{subtitle_suffix(usub, src_sub.suffix)}"
                    try:
                        copy_subtitle(info_hash, src_sub, dest_path)
                        created += 1
                        logger.info("   [uploaded] %s → %s", stored_name, dest_path)
                    except OSError as e:
                        logger.error("   复制上传字幕失败: %s → %s — %s", src_sub, dest_path, e)

            else:
                # ── TV mode: path template ──
                template = config.RSS_PATH_TEMPLATE
                from ..media.paths import format_download_path
                from ..metadata.nfo import generate_tv_show_nfo, generate_season_nfo

                seen_show_dirs: set[str] = set()
                seen_season_dirs: set[str] = set()

                for f in files:
                    torrent_path = f["torrent_path"]
                    is_sub = f.get("is_subtitle", False)
                    src_ext = subtitle_suffix(f, Path(torrent_path).suffix) if is_sub else Path(torrent_path).suffix

                    sub = subscription_path_context(f, series_name)

                    rel_path = format_download_path(
                        template, sub,
                        **episode_path_parameters(f),
                    ).lstrip("/")
                    # Replace extension with the actual source extension
                    rel_path = str(Path(rel_path).with_suffix(src_ext))

                    dest_path = Path(hardlink_root) / f.get("replacement_target", rel_path)
                    if f.get("replacement_target") and not is_sub:
                        dest_path = dest_path.with_name(dest_path.name + ".mam-bd-pending")
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    # Source: qBittorrent save_path / torrent_path
                    src_path = Path(save_path) / torrent_path

                    try:
                        if src_path.exists():
                            if is_sub:
                                copy_subtitle(info_hash, src_path, dest_path)
                            else:
                                install_hardlink(src_path, dest_path)
                                linked_videos.add(dest_path)
                            created += 1
                            logger.info("   %s → %s [%s]", src_path.name, dest_path, "copy" if is_sub else "hardlink")
                        else:
                            logger.warning("   源文件不存在: %s", src_path)
                    except OSError as e:
                        logger.error("   创建文件失败: %s → %s — %s", src_path, dest_path, e)

                # Copy user-uploaded subtitles
                for usub in uploaded_subtitles:
                    stored_name = usub.get("stored_filename", "")
                    src_sub = subtitle_dir / stored_name
                    if not src_sub.exists():
                        logger.warning("   上传的字幕文件不存在: %s", src_sub)
                        continue

                    sub = subscription_path_context(usub, series_name)

                    rel_path = format_download_path(
                        template, sub,
                        **episode_path_parameters(usub),
                    ).lstrip("/")
                    rel_path = str(Path(rel_path).with_suffix(subtitle_suffix(usub, src_sub.suffix)))

                    dest_path = Path(hardlink_root) / rel_path
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    try:
                        copy_subtitle(info_hash, src_sub, dest_path)
                        created += 1
                        logger.info("   [uploaded] %s → %s", stored_name, dest_path)
                    except OSError as e:
                        logger.error("   复制上传字幕失败: %s → %s — %s", src_sub, dest_path, e)

            # ── Generate NFO files (skipped if already done pre-resume) ──
            # Movie: always skip (pre-generated or skipped entirely)
            # TV: generate inline if skip_nfo is False
            nfo_generated = 0
            if movie_meta:
                logger.info("电影 NFO 已预生成，跳过内联 NFO 生成 [%s]", torrent_name)
            elif skip_nfo:
                logger.info("NFO 已预生成，跳过内联 NFO 生成 [%s]", torrent_name)
            else:
                for f in files:
                    is_sub = f.get("is_subtitle", False)
                    if is_sub:
                        continue

                    sub = subscription_path_context(f, series_name)

                    # Compute paths via template
                    rel_path = format_download_path(
                        template, sub,
                        **episode_path_parameters(f),
                    ).lstrip("/")
                    file_stem = Path(rel_path).stem
                    season_dir = Path(hardlink_root) / Path(rel_path).parent
                    show_dir = season_dir.parent
                    season_dir.mkdir(parents=True, exist_ok=True)

                    # tvshow.nfo (once per show_dir)
                    show_key = str(show_dir)
                    if show_key not in seen_show_dirs:
                        seen_show_dirs.add(show_key)
                        show_nfo_exists = (show_dir / "tvshow.nfo").exists()
                        generate_tv_show_nfo(
                            title=f.get("tmdb_show_name", ""),
                            original_title=f.get("bangumi_show_name", ""),
                            plot="",
                            output_dir=str(show_dir),
                        )
                        nfo_generated += 1
                        if not show_nfo_exists:
                            logger.info("NFO [%s tvshow.nfo] 字段来源：标题=TMDB 映射；原名=Bangumi 映射；简介=空", show_dir)
                        logger.info("   tvshow.nfo → %s", show_dir / "tvshow.nfo")

                    # season.nfo (once per season_dir)
                    season_key = str(season_dir)
                    if season_key not in seen_season_dirs:
                        seen_season_dirs.add(season_key)
                        season_nfo_exists = (season_dir / "season.nfo").exists()
                        bgm_id = f["episode_mapping"]["bangumi"]["subject_id"] or 0
                        tmdb_season = f["episode_mapping"]["tmdb"]["season_number"] or 0
                        generate_season_nfo(
                            title=f"Season {tmdb_season}",
                            original_title="",
                            plot="",
                            premiered="",
                            season_number=tmdb_season,
                            bangumi_id=bgm_id,
                            output_dir=str(season_dir),
                        )
                        nfo_generated += 1
                        if not season_nfo_exists:
                            logger.info("NFO [%s season.nfo] 字段来源：季号=TMDB 映射；Bangumi ID=Bangumi 映射；简介=空", season_dir)
                        logger.info("   season.nfo → %s", season_dir / "season.nfo")

                    # Resolve the already confirmed mapping; no compatibility numbering.
                    from ..metadata.resolution import resolve_nfo_episode
                    from ...domain.episode_metadata_adapters import provider_metadata_candidates
                    from ..metadata.nfo.nfo_xml import generate_episode_nfo
                    resolved = await resolve_nfo_episode(f["episode_mapping"], provider_metadata_candidates(),
                        show_name=f.get("tmdb_show_name", ""))
                    resolved["metadata"]["original_title"] = f.get("bangumi_show_name", "")
                    resolved["provenance"]["original_title"] = "display_context"
                    generate_episode_nfo(resolved, show_name=f.get("tmdb_show_name", ""),
                        bangumi_subject_name=f.get("bangumi_show_name", ""),
                        output_dir=str(season_dir), file_stem=file_stem)
                    nfo_generated += 1
                    logger.info("   episode.nfo → %s", season_dir / f"{file_stem}.nfo")

            logger.info("下载后处理完成 [%s]: 创建了 %d 个文件, 生成了 %d 个 NFO", torrent_name, created, nfo_generated)

            return linked_videos

    logger.warning("下载监控超时 [%s] (24h)", torrent_name)
    return None
