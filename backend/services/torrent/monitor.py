"""Torrent download monitor — poll qBittorrent, create hardlinks, copy subtitles.

Background task spawned by the torrent download route.  Cleanup of the
task registry (``state._download_tasks``) is handled by the caller so
this module stays independent of the API layer.
"""

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path

from ... import config
from ...clients.qbittorrent import get_torrents_by_hashes, login as qb_login
from ...utils.paths import SUBTITLE_DIR

logger = logging.getLogger(__name__)


def _subtitle_suffix(item: dict, fallback: str) -> str:
    suffix = item.get("subtitle_suffix", "")
    return suffix if re.fullmatch(r"(?:\.sub[1-9][0-9]*)?\.[a-zA-Z0-9]+", suffix) else fallback



def build_processing(context: dict) -> dict:
    """Resolve confirmed selections into file operations before download starts."""
    from ..nfo import format_download_path

    root = Path(context["hardlink_root"])
    movie_meta = context.get("movie_meta")
    torrent_name = context["torrent_name"]
    subtitle_dir = SUBTITLE_DIR / _sanitize(torrent_name)
    operations: list[dict] = []

    def destination(item: dict, suffix: str, *, is_subtitle: bool) -> Path:
        if is_subtitle:
            suffix = _subtitle_suffix(item, suffix)
        if movie_meta:
            title = movie_meta["tmdb_name"]
            return root / title / f"{title}{suffix}"
        sub = _make_sub_for_path(item, context.get("series_name", ""))
        rel = format_download_path(
            config.RSS_PATH_TEMPLATE, sub,
            tvdb_episode=item.get("tvdb_episode") or 0,
            tmdb_episode=item.get("tmdb_episode") or 0,
        ).lstrip("/")
        rel = str(Path(rel).with_suffix(suffix))
        target = root / item.get("replacement_target", rel)
        if item.get("replacement_target") and not is_subtitle:
            target = target.with_name(target.name + ".mam-bd-pending")
        return target

    for item in context["files"]:
        is_subtitle = bool(item.get("is_subtitle"))
        operation = {
            "torrent_path": item["torrent_path"],
            "target_path": str(destination(item, Path(item["torrent_path"]).suffix, is_subtitle=is_subtitle)),
            "action": "copy" if is_subtitle else "hardlink",
        }
        if context.get("replace_bangumi_id") is not None and not is_subtitle:
            operation["bangumi_sort"] = int(item["bangumi_sort"])
        operations.append(operation)

    for item in context.get("uploaded_subtitles", []):
        stored = item.get("stored_filename", "")
        operations.append({
            "source_path": str(subtitle_dir / stored),
            "target_path": str(destination(item, Path(stored).suffix, is_subtitle=True)),
            "action": "copy",
        })

    return {
        "mode": "movie" if movie_meta else "tv",
        "replace_bangumi_id": context.get("replace_bangumi_id"),
        "files": operations,
    }


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
                    staged = dest.with_name(dest.name + ".mam-new")
                    staged.unlink(missing_ok=True)
                    os.link(src, staged)
                    os.replace(staged, dest)
                    linked.add(dest)
                else:
                    shutil.copy2(src, dest)
            except OSError:
                logger.exception("Torrent 后处理失败: %s → %s", src, dest)
        return linked
    logger.warning("下载监控超时 [%s] (24h)", torrent_name)
    return None

def _sanitize(name: str) -> str:
    """Remove characters that are illegal in directory / file names."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()



def _make_sub_for_path(f: dict, series_name: str = "") -> dict:
    """Build a pseudo-subscription dict for :func:`format_download_path`."""
    bgm_name = f.get("bangumi_show_name", "")
    return {
        "name": bgm_name,
        "series_name": series_name or bgm_name,
        "bgm": {
            "subject_name": bgm_name,
            "season": 1,
        },
        "tvdb": {
            "season": f.get("tvdb_season") or f.get("tmdb_season", 1),
        },
        "tmdb": {
            "season": f.get("tmdb_season", 1),
        },
    }



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
    subtitle_dir = SUBTITLE_DIR / _sanitize(torrent_name)

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
                    src_ext = _subtitle_suffix(f, Path(torrent_path).suffix) if is_sub else Path(torrent_path).suffix
                    src_path = Path(save_path) / torrent_path

                    if is_sub:
                        dest_path = movie_dir / f"{tmdb_name}{src_ext}"
                    else:
                        dest_path = movie_dir / f"{tmdb_name}{src_ext}"

                    try:
                        if src_path.exists():
                            if is_sub:
                                shutil.copy2(src_path, dest_path)
                            else:
                                staged = dest_path.with_name(dest_path.name + ".mam-new")
                                staged.unlink(missing_ok=True)
                                os.link(src_path, staged)
                                os.replace(staged, dest_path)
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
                    dest_path = movie_dir / f"{tmdb_name}{_subtitle_suffix(usub, src_sub.suffix)}"
                    try:
                        shutil.copy2(src_sub, dest_path)
                        created += 1
                        logger.info("   [uploaded] %s → %s", stored_name, dest_path)
                    except OSError as e:
                        logger.error("   复制上传字幕失败: %s → %s — %s", src_sub, dest_path, e)

            else:
                # ── TV mode: path template ──
                template = config.RSS_PATH_TEMPLATE
                from ..nfo import format_download_path
                from ..nfo import (
                    write_episode_files,
                    generate_tv_show_nfo,
                    generate_season_nfo,
                )

                seen_show_dirs: set[str] = set()
                seen_season_dirs: set[str] = set()

                for f in files:
                    torrent_path = f["torrent_path"]
                    is_sub = f.get("is_subtitle", False)
                    src_ext = _subtitle_suffix(f, Path(torrent_path).suffix) if is_sub else Path(torrent_path).suffix

                    sub = _make_sub_for_path(f, series_name)
                    tvdb_ep = f.get("tvdb_episode") or 0
                    tmdb_ep = f.get("tmdb_episode") or 0

                    rel_path = format_download_path(
                        template, sub,
                        tvdb_episode=tvdb_ep, tmdb_episode=tmdb_ep,
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
                                shutil.copy2(src_path, dest_path)
                            else:
                                staged = dest_path.with_name(dest_path.name + ".mam-new")
                                staged.unlink(missing_ok=True)
                                os.link(src_path, staged)
                                os.replace(staged, dest_path)
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

                    sub = _make_sub_for_path(usub, series_name)
                    tvdb_ep = usub.get("tvdb_episode") or 0
                    tmdb_ep = usub.get("tmdb_episode") or 0

                    rel_path = format_download_path(
                        template, sub,
                        tvdb_episode=tvdb_ep, tmdb_episode=tmdb_ep,
                    ).lstrip("/")
                    rel_path = str(Path(rel_path).with_suffix(_subtitle_suffix(usub, src_sub.suffix)))

                    dest_path = Path(hardlink_root) / rel_path
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    try:
                        shutil.copy2(src_sub, dest_path)
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

                    sub = _make_sub_for_path(f, series_name)
                    tvdb_ep = f.get("tvdb_episode") or 0
                    tmdb_ep = f.get("tmdb_episode") or 0

                    # Compute paths via template
                    rel_path = format_download_path(
                        template, sub,
                        tvdb_episode=tvdb_ep, tmdb_episode=tmdb_ep,
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
                        bgm_id = f.get("bangumi_id", 0)
                        tmdb_season = f.get("tmdb_season", 0)
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

                    # Episode NFO
                    await write_episode_files(
                        {},  # tmdb_ep (empty = skip thumb download)
                        season_number=f.get("tmdb_season", 0),
                        episode_number=f.get("tmdb_episode", 0),
                        bangumi_ep_id=f.get("bangumi_ep_id"),
                        show_name=f.get("tmdb_show_name", ""),
                        original_name=f.get("bangumi_show_name", ""),
                        bangumi_subject_name=f.get("bangumi_show_name", ""),
                        studios=[],
                        rating=0,
                        output_dir=str(season_dir),
                        thumb_source="tmdb",
                        file_stem=file_stem,
                    )
                    nfo_generated += 1
                    logger.info("   episode.nfo → %s", season_dir / f"{file_stem}.nfo")

            logger.info("下载后处理完成 [%s]: 创建了 %d 个文件, 生成了 %d 个 NFO", torrent_name, created, nfo_generated)

            return linked_videos

    logger.warning("下载监控超时 [%s] (24h)", torrent_name)
    return None
