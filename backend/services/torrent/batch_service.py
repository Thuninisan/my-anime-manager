"""Batch torrent processing orchestration with qBittorrent.

Split into two phases:
  1. build_preview() — local analysis only (parse torrent, search TMDB/Bangumi)
  2. execute_confirm() — qBittorrent + NFO + images + rename + resume
"""

import logging
import re
from pathlib import Path
import shutil

from ...clients import bangumi as bgm_client
from ...clients.qbittorrent import (
    login as qb_login,
    add_torrent,
    get_torrent_files,
    rename_file,
    resume_torrent,
)
from ... import config
from .. import tmdb as tmdb_service
from .. import bangumi as bangumi_service
from ..mapper import find_target_entry
from ..nfo import write_episode_files
from ..nfo.images import download_season_poster, download_show_images
from ..nfo.nfo_xml import generate_season_nfo, generate_tv_show_nfo
from ..nfo.plot_fallback import resolve_episode_plot, resolve_season_plot
from ...utils.torrent_parser import parse_qbit_file_list
from ...utils.torrent_file_reader import read_torrent_file_list

logger = logging.getLogger(__name__)

# Subtitle file extensions (collected from torrent for the frontend Sub badge)
_SUBTITLE_EXTENSIONS: set[str] = {".ass", ".ssa", ".srt", ".sub", ".idx", ".vtt", ".ttml", ".sbv", ".dfxp"}


def _sanitize_dir_name(name: str | None) -> str:
    """Remove illegal characters from a directory name."""
    if not name:
        return "unknown"
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


def _pick_show_name(file_list: list[dict]) -> tuple[str, str | None]:
    """Pick the show name from the parsed file list."""
    names = [f["showName"] for f in file_list if f.get("showName")]
    if not names:
        return "", None
    from collections import Counter
    best = Counter(names).most_common(1)[0][0]
    year_match = re.search(r"[\s\-–—]*(\d{4})$", best)
    year = year_match.group(1) if year_match else None
    best = re.sub(r"[\s\-–—]*\d{4}$", "", best).strip()
    return best, year


def _find_entry_in_chain(target_title: str, chain: list[dict]) -> int:
    """Find the chain entry whose name best matches the target title."""
    target_lower = target_title.lower()
    best_id = chain[0]["id"]
    best_score = 0
    for entry in chain:
        name = (entry.get("name") or "").lower()
        name_cn = (entry.get("name_cn") or "").lower()
        score = 0
        if name == target_lower or name_cn == target_lower:
            score = 100
        elif target_lower in name or name in target_lower:
            score = 50
        elif target_lower in name_cn or name_cn in target_lower:
            score = 40
        if score > best_score:
            best_score = score
            best_id = entry["id"]
    return best_id


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: Local preview (no qBittorrent, no disk writes)
# ═══════════════════════════════════════════════════════════════════════

async def build_preview(torrent_path: str) -> dict:
    """Analyse a .torrent file locally — no qBittorrent interaction.

    Reads the file list directly from the torrent via bencode, then runs
    TMDB + Bangumi search and episode matching.  Returns everything the
    frontend needs for the 4-card preview.

    Args:
        torrent_path: Path to .torrent file (will be kept for confirm phase)

    Returns:
        A ``preview_data`` dict (includes *torrent_path* for confirm).
    """
    torrent_name = Path(torrent_path).stem

    # ── Step 1: Read file list directly from the .torrent file ──
    logger.debug("📋 读取种子文件内容...")
    try:
        file_list = read_torrent_file_list(torrent_path)
        logger.debug(f"   → {len(file_list)} 个文件")
    except Exception as e:
        raise RuntimeError(f"无法解析种子文件: {e}") from e

    # ── Collect subtitle files from the torrent ──
    subtitle_files: list[str] = [
        Path(f["name"]).name
        for f in file_list
        if Path(f["name"]).suffix.lower() in _SUBTITLE_EXTENSIONS
    ]
    if subtitle_files:
        logger.debug(f"   📝 {len(subtitle_files)} 个字幕文件")

    # ── Step 2: Parse file list ──
    result = parse_qbit_file_list(file_list, torrent_name)
    episodes = result["episodes"]
    extras = result["extras"]
    if not episodes:
        raise RuntimeError("没有找到可处理的剧集文件")

    episodes.sort(key=lambda e: (e["season"], e["episode"]))

    # ── Step 3: Determine show name, search TMDB ──
    show_name, show_year = _pick_show_name(episodes)
    logger.debug(f'🔍 从文件名推断节目名: "{show_name}"')
    if show_year:
        logger.debug(f'   从文件名提取年份: {show_year}')

    logger.info("📡 === TMDB 阶段 ===")
    tv_show = await tmdb_service.search_tv_show(show_name, prefer_year=show_year)

    if not tv_show and episodes[0]["showName"] != show_name:
        logger.warning(f'   用完整文件名重试: "{episodes[0]["showName"]}"')
        tv_show = await tmdb_service.search_tv_show(
            episodes[0]["showName"], prefer_year=show_year
        )

    if not tv_show:
        raise RuntimeError(f'TMDB 未找到节目，尝试的名称: "{show_name}"')

    # ── Step 4: TMDB details + season map ──
    detail = await tmdb_service.get_tv_show_detail(tv_show["id"])
    jp_name = detail.get("original_name") or tv_show["name"]
    original_name = (
        detail.get("original_name")
        or tv_show.get("original_name")
        or tv_show["name"]
    )
    logger.debug(f"   日文原名: {jp_name}")
    logger.debug(f"   首播日期: {detail.get('first_air_date', '未知')}")

    logger.debug("📊 构建 TMDB 季→集映射...")
    season_map = await tmdb_service.build_season_episode_map(tv_show["id"])

    # ── Step 5: Bangumi search + build chain ──
    logger.debug("📚 === Bangumi 阶段 ===")
    bgm_results = await bangumi_service.search_bangumi(jp_name)
    if not bgm_results:
        logger.warning(f'   用中文名重试: "{tv_show["name"]}"')
        bgm_results = await bangumi_service.search_bangumi(tv_show["name"])
    if (
        not bgm_results
        and detail.get("name") != jp_name
        and detail.get("name") != tv_show["name"]
    ):
        logger.warning(f'   用 TMDB 名称重试: "{detail["name"]}"')
        bgm_results = await bangumi_service.search_bangumi(detail["name"])
    if not bgm_results:
        raise RuntimeError("Bangumi 未找到该节目")

    logger.debug("🔗 遍历 Bangumi 条目链...")
    first_result_id = bgm_results[0]["id"]
    initial_subject = await bgm_client.get_subject(first_result_id)
    init_name = initial_subject.get("name_cn") or initial_subject["name"]
    logger.debug(f"   搜索命中: {init_name} [id: {initial_subject['id']}]")

    first_id = await bangumi_service.find_first_in_chain(initial_subject["id"])
    logger.debug(f"   起始条目 ID: {first_id}")

    chain, skipped_entries = await bangumi_service.build_bangumi_chain(first_id)
    if len(chain) < len(season_map):
        logger.warning(f"⚠️ 续集链只有 {len(chain)} 个条目，但 TMDB 有 {len(season_map)} 季")
        logger.debug("   尝试按日期排序的备选方案...")
        chain, skipped_entries2 = await bangumi_service.build_chain_by_date(bgm_results)
        # Merge skipped entries from both attempts (dedupe by id)
        seen_ids = {s["id"] for s in skipped_entries}
        for s in skipped_entries2:
            if s["id"] not in seen_ids:
                skipped_entries.append(s)
                seen_ids.add(s["id"])
    if not chain:
        raise RuntimeError("未找到任何有效的 Bangumi 条目")

    # ── Scan all relations of chain entries for side stories / recaps ──
    side_entries = await bangumi_service.collect_side_entries(chain, skipped_entries)
    skipped_entries.extend(side_entries)

    start_entry_id = _find_entry_in_chain(jp_name, chain)
    logger.debug(f"   🎯 匹配条目: id={start_entry_id}")

    earliest = min(chain, key=lambda e: e.get("date") or "9999-99-99")
    show_dir_name = _sanitize_dir_name(earliest.get("name_cn") or earliest["name"])
    output_root = str(Path(config.QBITTORRENT_SAVE_PATH) / show_dir_name)

    # ── Step 6: Re-search TMDB with Bangumi-derived name ──
    logger.debug("📡 用 Bangumi 名称重新查找 TMDB 节目级数据...")
    tvshow_search_name = earliest.get("name_cn") or earliest["name"]
    tvshow_result = await tmdb_service.search_tv_show(tvshow_search_name)
    if tvshow_result:
        tvshow_detail = await tmdb_service.get_tv_show_detail(tvshow_result["id"])
        tvshow_title = tvshow_result["name"]
        tvshow_original = (
            tvshow_detail.get("original_name")
            or tvshow_result.get("original_name")
            or tvshow_result["name"]
        )
        tvshow_tmdb_id = tvshow_result["id"]
        logger.debug(f"   ✅ 命中: {tvshow_title} [TMDB id: {tvshow_tmdb_id}]")
    else:
        logger.warning(f"   ⚠️ 未命中，使用原始搜索结果")
        tvshow_detail = detail
        tvshow_title = tv_show["name"]
        tvshow_original = original_name
        tvshow_tmdb_id = tv_show["id"]

    # ── Step 7: Preload Bangumi episode lists for ALL chain entries ──
    logger.debug("📡 预加载 Bangumi 剧集列表...")
    bgm_episode_cache: dict[int, list[dict]] = {}
    start_idx = next((i for i, e in enumerate(chain) if e["id"] == start_entry_id), 0)

    for ci, entry in enumerate(chain):
        if entry.get("eps", 0) > 0:
            try:
                eps_list = await bgm_client.get_episodes(entry["id"])
                bgm_episode_cache[entry["id"]] = eps_list
                ename = entry.get("name_cn") or entry["name"]
                logger.debug(f"   [{ename}] → {len(eps_list)} 集")
            except Exception as exc:
                ename = entry.get("name_cn") or entry["name"]
                logger.warning(f"   ⚠️ 获取 {ename} 剧集失败: {exc}")

    # ── Preload episode lists for skipped entries (番外篇/总集篇 etc.) ──
    if skipped_entries:
        for entry in skipped_entries:
            if entry.get("eps", 0) > 0:
                try:
                    eps_list = await bgm_client.get_episodes(entry["id"])
                    bgm_episode_cache[entry["id"]] = eps_list
                    ename = entry.get("name_cn") or entry["name"]
                    kind = entry.get("kind", "番外")
                    logger.debug(f"   [{kind}] {ename} → {len(eps_list)} 集")
                except Exception as exc:
                    ename = entry.get("name_cn") or entry["name"]
                    logger.warning(f"   ⚠️ 获取 {ename} 剧集失败: {exc}")

    # ── Step 8: Build episode mappings + planned rename paths ──
    logger.debug(f"📊 构建剧集映射与重命名计划 (共 {len(episodes)} 个)...")

    episode_previews: list[dict] = []
    all_file_mappings: list[dict] = []

    for file in episodes:
        tmdb_season = file["season"]
        tmdb_ep_num = file["episode"]
        old_torrent_path = file["torrentPath"]
        filename = file["fileName"]

        season_data = season_map.get(tmdb_season)
        tmdb_ep = None
        if season_data and 1 <= tmdb_ep_num <= len(season_data["episodes"]):
            tmdb_ep = season_data["episodes"][tmdb_ep_num - 1]

        if tmdb_season == 0:
            # ── Specials (S00) ──
            # Use first Bangumi entry for subject name; don't run the mapper
            target_subject = chain[0] if chain else None
            within_ep_num = tmdb_ep_num
            season_number = 0
        else:
            mapping = find_target_entry(
                chain, start_entry_id=start_entry_id,
                season=tmdb_season, episode=tmdb_ep_num,
            )
            target_subject = mapping["targetSubject"] if mapping else None
            within_ep_num = mapping["withinEpNum"] if mapping else tmdb_ep_num
            season_number = (
                next((i + 1 for i, e in enumerate(chain) if e["id"] == target_subject["id"]), 1)
                if target_subject else 1
            )

        bgm_eps = bgm_episode_cache.get(target_subject["id"], []) if target_subject else []
        bgm_ep = bangumi_service.match_episode(bgm_eps, within_ep_num)
        episode_number = (bgm_ep.get("sort") if bgm_ep else None) or within_ep_num
        bangumi_ep_id = bgm_ep.get("id") if bgm_ep else None
        bangumi_subject_name = (
            target_subject.get("name_cn") or target_subject["name"]
            if target_subject else tv_show["name"]
        )

        ext = Path(filename).suffix
        if tmdb_season == 0:
            episode_str = f"S00E{tmdb_ep_num:02d}"
            new_file_path = (
                f"{show_dir_name}/Specials/"
                f"{_sanitize_dir_name(bangumi_subject_name)} {episode_str}{ext}"
            )
        else:
            episode_str = f"{episode_number:02d}"
            new_file_path = (
                f"{show_dir_name}/Season {season_number}/"
                f"{_sanitize_dir_name(bangumi_subject_name)} {episode_str}{ext}"
            )
        all_file_mappings.append({
            "oldPath": old_torrent_path,
            "newPath": new_file_path,
            "type": "episode",
        })

        episode_previews.append({
            "fileName": filename, "torrentPath": old_torrent_path,
            "showName": file["showName"], "season": tmdb_season,
            "episode": tmdb_ep_num, "seasonNumber": season_number,
            "episodeNumber": episode_number,
            "bangumiSubjectName": bangumi_subject_name,
            "bangumiEpId": bangumi_ep_id,
            "tmdbEpName": tmdb_ep["name"] if tmdb_ep else "",
            "tmdbEpId": tmdb_ep["tmdbId"] if tmdb_ep else 0,
        })

        if tmdb_ep:
            logger.debug(f"   ✅ {filename}")
            if tmdb_season == 0:
                logger.debug(f"      S00E{tmdb_ep_num:02d} | "
                      f"TMDB: {tmdb_ep['name']} | BGM epid: {bangumi_ep_id or '无'}")
            else:
                logger.debug(f"      S{season_number:02d}E{episode_number:02d} | "
                      f"TMDB: {tmdb_ep['name']} | BGM epid: {bangumi_ep_id or '无'}")
        else:
            logger.warning(f"   ⚠️ 跳过 {filename}: TMDB 中无匹配剧集")

    # ── Step 9: Build tvshow block ──
    tvshow_block = {
        "title": tvshow_title,
        "original_title": tvshow_original,
        "plot": tvshow_detail.get("overview", ""),
        "premiered": tvshow_detail.get("first_air_date", ""),
        "tmdb_id": tvshow_tmdb_id,
        "genres": tvshow_detail.get("genres", []),
        "studios": detail.get("studios", []),
        "rating": tvshow_detail.get("vote_average", 0),
        "status": tvshow_detail.get("status", ""),
    }

    # ── Step 10: Build seasons block ──
    # Collect unique season_numbers used by episodes
    used_season_numbers: set[int] = {ep["seasonNumber"] for ep in episode_previews}
    start_idx = next((i for i, e in enumerate(chain) if e["id"] == start_entry_id), 0)
    seasons_block: dict[str, dict] = {}

    logger.debug(f"📡 获取季信息...")
    for sn in sorted(used_season_numbers):
        if sn == 0:
            # Specials season — use first Bangumi entry for metadata
            entry = chain[0] if chain else None
            if entry is None:
                continue
            try:
                full = await bgm_client.get_subject(entry["id"])
            except Exception:
                full = None
            tmdb_season_data = season_map.get(0)
            tmdb_season_name = tmdb_season_data["name"] if tmdb_season_data else "Specials"
            seasons_block[str(sn)] = {
                "bgm_id": entry["id"],
                "bgm_title": entry.get("name_cn") or entry["name"],
                "bgm_original": entry["name"],
                "bgm_plot": full.get("summary", "") if full else "",
                "bgm_premiered": full.get("date", "") or entry.get("date", ""),
                "bgm_images": full.get("images") if full else None,
                "tmdb_season_name": tmdb_season_name,
            }
            logger.debug(f"   ✅ S00 (Specials): {entry.get('name_cn') or entry['name']}")
        else:
            ci = start_idx + (sn - 1)
            if ci < 0 or ci >= len(chain):
                continue
            entry = chain[ci]
            # Pre-fetch full subject for NFO + poster
            try:
                full = await bgm_client.get_subject(entry["id"])
            except Exception:
                full = None

            # Find which TMDB season maps to this Bangumi chain position
            matched_ep = next((ep for ep in episode_previews if ep["seasonNumber"] == sn), None)
            tmdb_s = matched_ep["season"] if matched_ep else sn
            tmdb_season_data = season_map.get(tmdb_s)
            tmdb_season_name = tmdb_season_data["name"] if tmdb_season_data else f"Season {tmdb_s}"

            seasons_block[str(sn)] = {
                "bgm_id": entry["id"],
                "bgm_title": entry.get("name_cn") or entry["name"],
                "bgm_original": entry["name"],
                "bgm_plot": full.get("summary", "") if full else "",
                "bgm_premiered": full.get("date", "") or entry.get("date", ""),
                "bgm_images": full.get("images") if full else None,
                "tmdb_season_name": tmdb_season_name,
            }
            logger.debug(f"   ✅ S{sn:02d}: {entry.get('name_cn') or entry['name']}")

    # ── Step 11: Build episodes block (keyed by filename) ──
    episodes_block: dict[str, dict] = {}

    for ep in episode_previews:
        tmdb_season = ep["season"]
        tmdb_ep_num = ep["episode"]
        season_number = ep["seasonNumber"]
        episode_number = ep["episodeNumber"]
        filename = ep["fileName"]

        # Get full TMDB episode data from season_map
        season_data = season_map.get(tmdb_season)
        tmdb_ep = None
        if season_data and 1 <= tmdb_ep_num <= len(season_data["episodes"]):
            tmdb_ep = season_data["episodes"][tmdb_ep_num - 1]

        # Compute old/new paths from all_file_mappings
        mapping_info = next((m for m in all_file_mappings if m.get("oldPath") == ep["torrentPath"]), None)
        new_path = mapping_info["newPath"] if mapping_info else ""

        ep_block = {
            "oldPath": ep["torrentPath"],
            "newPath": new_path,
            "tmdb_season": tmdb_season,
            "season_number": season_number,
            "episode_number": episode_number,
            "bangumi_subject_name": ep["bangumiSubjectName"],
            "bangumi_ep_id": ep.get("bangumiEpId"),
            "tmdb": {
                "name": tmdb_ep["name"] if tmdb_ep else "",
                "overview": tmdb_ep.get("overview", "") if tmdb_ep else "",
                "air_date": tmdb_ep.get("airDate", "") if tmdb_ep else "",
                "runtime": tmdb_ep.get("runtime", 0) if tmdb_ep else 0,
                "id": tmdb_ep["tmdbId"] if tmdb_ep else 0,
                "still_path": tmdb_ep.get("stillPath", "") if tmdb_ep else "",
                "directors": tmdb_ep.get("directors", []) if tmdb_ep else [],
                "writers": tmdb_ep.get("writers", []) if tmdb_ep else [],
                "guest_stars": tmdb_ep.get("guestStars", []) if tmdb_ep else [],
            } if tmdb_ep else None,
        }
        episodes_block[filename] = ep_block

    # ── Step 12: Extra files ──
    season_counts: dict[int, int] = {}
    for ep in episodes:
        s = ep["season"]
        season_counts[s] = season_counts.get(s, 0) + 1
    dominant_season = max(season_counts.items(), key=lambda x: x[1])[0] if season_counts else 1

    extras_block: list[dict] = []
    for extra in extras:
        new_file_path = f"{show_dir_name}/Season {dominant_season}/Extra/{extra['fileName']}"
        extras_block.append({
            "oldPath": extra["torrentPath"],
            "newPath": new_file_path,
            "type": extra.get("type", "unknown"),
        })

    # ── Build tmdb_data for frontend dropdowns ──
    tmdb_data: dict[str, dict] = {}
    for sk, sv in season_map.items():
        eps_data: dict[str, dict] = {}
        for e in sv.get("episodes", []):
            eps_data[str(e["epNum"])] = {
                "name": e["name"],
                "overview": e.get("overview", ""),
                "air_date": e.get("airDate", ""),
                "runtime": e.get("runtime", 0),
                "id": e["tmdbId"],
                "still_path": e.get("stillPath", ""),
                "directors": e.get("directors", []),
                "writers": e.get("writers", []),
                "guest_stars": e.get("guestStars", []),
            }
        tmdb_data[str(sk)] = {
            "name": sv.get("name", f"Season {sk}"),
            "episodes": eps_data,
        }

    # ── Build bangumi_data for frontend dropdowns (all chain entries, not just used seasons) ──
    bangumi_data: dict[str, dict] = {}
    for idx, entry in enumerate(chain):
        bgm_id = entry["id"]
        sn_key = str(idx + 1)  # 1-based season number matching chain position
        eps_list: list[dict] = []
        for ep in bgm_episode_cache.get(bgm_id, []):
            eps_list.append({
                "sort": ep.get("sort") or ep.get("ep", 0),
                "id": ep["id"],
                "name": ep.get("name_cn") or ep.get("name", ""),
            })
        eps_list.sort(key=lambda x: x["sort"])
        bangumi_data[sn_key] = {
            "name": entry.get("name_cn") or entry["name"],
            "subject_id": bgm_id,
            "episodes": eps_list,
        }

    # ── Append skipped entries (番外篇/总集篇等) with 900+ keys ──
    EXTRA_KEY_BASE = 900
    for idx, entry in enumerate(skipped_entries):
        bgm_id = entry["id"]
        sn_key = str(EXTRA_KEY_BASE + idx)
        eps_list: list[dict] = []
        for ep in bgm_episode_cache.get(bgm_id, []):
            eps_list.append({
                "sort": ep.get("sort") or ep.get("ep", 0),
                "id": ep["id"],
                "name": ep.get("name_cn") or ep.get("name", ""),
            })
        eps_list.sort(key=lambda x: x["sort"])
        bangumi_data[sn_key] = {
            "name": entry.get("name_cn") or entry["name"],
            "subject_id": bgm_id,
            "episodes": eps_list,
            "kind": entry.get("kind", "番外篇"),
        }

    preview_data = {
        "torrent_path": torrent_path,
        "torrent_name": torrent_name,
        "save_path": config.QBITTORRENT_SAVE_PATH,
        "output_root": output_root,
        "tvshow": tvshow_block,
        "seasons": seasons_block,
        "episodes": episodes_block,
        "extras": extras_block,
        "tmdb_data": tmdb_data,
        "bangumi_data": bangumi_data,
        "subtitles": subtitle_files,
    }

    logger.info("批量预览已就绪: 剧集=%d 额外文件=%d 季=%d 输出目录=%s",
                len(episodes_block), len(extras_block), len(seasons_block), output_root)
    logger.debug(f"   剧集文件: {len(episodes_block)} 个")
    logger.debug(f"   额外文件: {len(extras_block)} 个")
    logger.debug(f"   季: {len(seasons_block)} 个")
    logger.debug(f"   输出目录: {output_root}")

    return preview_data


# ═══════════════════════════════════════════════════════════════════════
# Shared NFO / image generator (used by both torrent and RSS flows)
# ═══════════════════════════════════════════════════════════════════════

async def generate_metadata_collection(
    tvshow: dict,
    seasons: dict,
    episodes: dict,
    output_root: str,
) -> dict:
    """Generate all NFO files and download images for a torrent batch.

    Pure file-writing — no TMDB / Bangumi API calls.  All metadata
    must already be present in *tvshow*, *seasons* and *episodes*
    (exactly as built by :func:`build_preview`).

    Args:
        tvshow:  ``{title, original_title, tmdb_id, plot, premiered,
            genres, studios, rating, status}``.
        seasons: ``{sn: {bgm_id, bgm_title, bgm_original, bgm_plot,
            bgm_premiered, bgm_images}}`` keyed by season number string.
        episodes: ``{filename: {season_number, episode_number,
            bangumi_ep_id, bangumi_subject_name, tmdb: {...}}}``.
        output_root:  Show directory path (tvshow.nfo is written here,
            season / episode content goes into ``Season N/`` subdirs).

    Returns:
        ``{nfoGenerated: int, imagesDownloaded: int}``.
    """
    summary = {"nfoGenerated": 0, "imagesDownloaded": 0}

    # ── tvshow.nfo ──────────────────────────────────────────────────
    logger.debug("📄 生成 tvshow.nfo...")

    # Fetch TMDB zh-CN detail + plot fallback
    from ...clients import tmdb as tmdb_client
    title_zh = tvshow["title"]
    original_zh = tvshow["original_title"]
    plot_zh = tvshow["plot"]
    title_source = "预览数据"
    original_source = "预览数据"
    plot_source = "预览数据" if plot_zh else "空简介"
    logger.info("NFO [%s tvshow.nfo 简介] 预览数据：%s", output_root,
                "有简介" if plot_zh else "无简介")
    try:
        detail_zh = await tmdb_client.get_tv_detail(tvshow["tmdb_id"], language="zh-CN")
        if detail_zh.get("name"):
            title_zh = detail_zh["name"]
            title_source = "TMDB zh-CN"
        if detail_zh.get("original_name"):
            original_zh = detail_zh["original_name"]
            original_source = "TMDB zh-CN"
        if detail_zh.get("overview"):
            plot_zh = detail_zh["overview"]
            plot_source = "TMDB zh-CN"
        logger.info("NFO [%s tvshow.nfo 简介] TMDB zh-CN：%s", output_root,
                    "命中" if detail_zh.get("overview") else "无简介，使用预览数据")
    except Exception:
        logger.warning("NFO [%s tvshow.nfo 简介] TMDB zh-CN：请求失败，使用预览数据", output_root, exc_info=True)

    # Fallback: if no Chinese overview, try Bangumi summary from first season
    if not plot_zh and seasons:
        logger.info("NFO [%s tvshow.nfo 简介] 尝试 Bangumi 季简介", output_root)
        first_season = next(iter(seasons.values()))
        season_source: list[str] = []
        season_plot = await resolve_season_plot(first_season.get("bgm_plot", ""),
                                                context=f"{output_root} tvshow.nfo",
                                                selected_source=season_source)
        if season_plot:
            plot_zh = season_plot
            plot_source = season_source[0]

    show_nfo_exists = (Path(output_root) / "tvshow.nfo").exists()
    nfo_path = generate_tv_show_nfo(
        title=title_zh,
        original_title=original_zh,
        plot=plot_zh,
        output_dir=output_root,
        tvdb_id=tvshow.get("tvdb_id", 0),
        tmdb_id=tvshow["tmdb_id"],
    )
    if not show_nfo_exists:
        logger.info("NFO [%s] 字段来源：标题=%s；原名=%s；简介=%s；TMDB ID=TMDB；TVDB ID=TVDB",
                    nfo_path, title_source, original_source, plot_source)
    logger.debug(f"   ✅ tvshow.nfo: {nfo_path}")
    summary["nfoGenerated"] += 1

    # ── Show-level images ───────────────────────────────────────────
    logger.debug("🖼️ 下载节目图片...")
    show_imgs = await download_show_images(tvshow["tmdb_id"], output_root)
    summary["imagesDownloaded"] += sum(1 for v in show_imgs.values() if v)

    # ── Season NFOs + posters ───────────────────────────────────────
    logger.debug("📄 生成季 NFO + 下载季海报...")
    for sk, season in sorted(seasons.items(), key=lambda x: int(x[0])):
        season_number = int(sk)
        season_dir = str(Path(output_root) / f"Season {season_number}")

        if season.get("bgm_images"):
            poster = await download_season_poster(
                {"images": season["bgm_images"]}, output_root, season_number,
            )
            if poster:
                logger.debug(f"   🖼️ Season {season_number} poster → {poster}")
                summary["imagesDownloaded"] += 1

        season_source = []
        season_plot = await resolve_season_plot(season.get("bgm_plot", ""),
                                                context=f"{season_dir} season.nfo",
                                                selected_source=season_source)
        season_nfo_exists = (Path(season_dir) / "season.nfo").exists()
        nfo = generate_season_nfo(
            title=season["bgm_title"],
            original_title=season["bgm_original"],
            plot=season_plot,
            premiered=season.get("bgm_premiered", ""),
            season_number=season_number,
            bangumi_id=season["bgm_id"],
            output_dir=season_dir,
        )
        if not season_nfo_exists:
            logger.info("NFO [%s] 字段来源：标题/原名/ID/播出日期=Bangumi；简介=%s",
                        nfo, season_source[0] if season_source else "空简介")
        logger.debug(f"   📄 Season {season_number} nfo → {nfo}")
        summary["nfoGenerated"] += 1

    # ── Episode NFOs + thumbnails ───────────────────────────────────
    logger.debug(f"📄 生成剧集 NFO + 下载缩略图 (共 {len(episodes)} 个)...")
    for filename, ep in episodes.items():
        tmdb = ep.get("tmdb")
        if not tmdb or not tmdb.get("id"):
            logger.warning(f"   ⚠️ 跳过 {filename}: 无 TMDB 剧集数据")
            continue

        season_dir = str(Path(output_root) / f"Season {ep['season_number']}")

        # Adapt torrent's tmdb block → normalised format for write_episode_files
        # Prefer per-episode original_name (Japanese title saved before
        # zh-CN overwrite), fall back to show-level original_title.
        ep_original = tmdb.get("original_name") or tvshow["original_title"]

        # ── TMDB zh-CN plot fallback (batch flow: TMDB only) ──────
        zh_plot = ""
        plot_source = []
        try:
            ep_context = f"{filename} / S{ep['season_number']:02d}E{ep['episode_number']:02d}"
            zh_plot = await resolve_episode_plot(
                tmdb_id=tvshow["tmdb_id"],
                tmdb_season=ep.get("tmdb_season", ep["season_number"]),
                tmdb_ep_num=ep["episode_number"],
                context=ep_context,
                selected_source=plot_source,
            )
            if zh_plot:
                tmdb["overview"] = zh_plot
        except Exception:
            logger.warning("NFO [%s 简介] 中文回退异常；保留原始简介", filename, exc_info=True)
        logger.info("NFO [%s 简介] 最终使用：%s", filename,
                    plot_source[0] if plot_source else "原始 TMDB 简介" if tmdb.get("overview") else "空简介")

        existing_nfos = {path.name for path in Path(season_dir).glob("*.nfo")}
        result = await write_episode_files(
            {
                "name": tmdb.get("name", ""),
                "overview": tmdb.get("overview", ""),
                "air_date": tmdb.get("air_date", ""),
                "runtime": tmdb.get("runtime", 0),
                "tmdb_id": tmdb["id"],  # torrent calls it "id", normalised is "tmdb_id"
                "still_path": tmdb.get("still_path", ""),
                "directors": tmdb.get("directors", []),
                "writers": tmdb.get("writers", []),
                "guest_stars": tmdb.get("guest_stars", []),
            },
            season_number=ep["season_number"],
            episode_number=ep["episode_number"],
            bangumi_ep_id=ep.get("bangumi_ep_id"),
            show_name=tvshow["title"],
            original_name=ep_original,
            bangumi_subject_name=ep["bangumi_subject_name"],
            studios=tvshow.get("studios", []),
            rating=tmdb.get("vote_average", 0) or 0,
            output_dir=season_dir,
        )
        if Path(result["nfo_path"]).name not in existing_nfos:
            logger.info("NFO [%s] 字段来源：标题/原名/日期/时长/评分=TMDB；简介=%s；"
                        "季集编号=预览映射；缩略图=%s",
                        result["nfo_path"],
                        plot_source[0] if plot_source else "原始 TMDB" if tmdb.get("overview") else "空简介",
                        "TMDB" if result["thumb_path"] else "无")
        if result["thumb_path"]:
            summary["imagesDownloaded"] += 1
        logger.debug(f"   ✅ {filename} → {result['nfo_path']}")
        summary["nfoGenerated"] += 1

    return summary


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: Write phase (qBittorrent + NFO + images)
# ═══════════════════════════════════════════════════════════════════════

async def execute_confirm(
    preview_data: dict,
    client: object | None = None,
) -> dict:
    """Execute the confirmed plan using the {tvshow, seasons, episodes} format."""
    torrent_path = preview_data["torrent_path"]
    torrent_name = preview_data["torrent_name"]
    save_path = preview_data["save_path"]
    output_root = preview_data["output_root"]

    tvshow = preview_data["tvshow"]
    seasons = preview_data.get("seasons", {})
    episodes = preview_data.get("episodes", {})
    extras = preview_data.get("extras", [])

    summary = {
        "nfoGenerated": 0,
        "imagesDownloaded": 0,
        "filesRenamed": 0,
        "showDirName": Path(output_root).name,
        "error": "",
    }

    # ── Login to qBittorrent (if no client provided) ──
    if client is None:
        logger.debug("🔗 连接 qBittorrent...")
        try:
            client = await qb_login(
                config.QBITTORRENT_URL,
                config.QBITTORRENT_USERNAME,
                config.QBITTORRENT_PASSWORD,
            )
            logger.debug("   ✅ qBittorrent 登录成功")
        except Exception as e:
            summary["error"] = str(e)
            return summary

    try:
        # ── Add torrent (paused) ──
        logger.debug("📥 添加种子到 qBittorrent（暂停）...")
        info_hash = await add_torrent(client, torrent_path, save_path, torrent_name)
        logger.debug(f"   ✅ 种子已添加 [hash: {info_hash[:12]}…]")

        # ── Rename files ──
        all_mappings = [
            {"oldPath": ep["oldPath"], "newPath": ep["newPath"], "type": "episode"}
            for ep in episodes.values()
        ] + extras
        if all_mappings:
            logger.debug(f"📝 重组文件结构 ({len(all_mappings)} 个文件)...")
            renamed = 0
            for mapping in all_mappings:
                try:
                    ok = await rename_file(client, info_hash, mapping["oldPath"], mapping["newPath"])
                    if ok:
                        logger.debug(f"   ✅ {mapping['oldPath']} → {mapping['newPath']}")
                        renamed += 1
                except Exception as e:
                    logger.warning(f"   ⚠️ 重命名失败: {mapping['oldPath']} → {mapping['newPath']} — {e}")
            summary["filesRenamed"] = renamed
            logger.debug(f"   已完成 {renamed}/{len(all_mappings)} 个")

        # ── Copy user-uploaded subtitles alongside media files ──
        # Subtitles are matched to video files by original filename stem.
        subtitle_src_dir = Path(__file__).parent.parent / "data" / "subtitles" / torrent_name
        if subtitle_src_dir.is_dir():
            logger.debug(f"📝 复制用户上传的字幕文件...")
            subtitle_files_list = list(subtitle_src_dir.iterdir())
            if subtitle_files_list:
                # Build a map of original filename stem → list of subtitle paths
                sub_stem_map: dict[str, list[Path]] = {}
                for sf in subtitle_files_list:
                    if sf.suffix.lower() in _SUBTITLE_EXTENSIONS:
                        stem = sf.stem.lower()
                        sub_stem_map.setdefault(stem, []).append(sf)

                copied = 0
                for mapping in all_mappings:
                    old_stem = Path(mapping["oldPath"]).stem.lower()
                    matching_subs = sub_stem_map.get(old_stem, [])
                    if not matching_subs:
                        continue
                    # The new path is relative; resolve it against output_root
                    new_video_path = Path(output_root) / mapping["newPath"]
                    new_video_dir = new_video_path.parent
                    new_video_dir.mkdir(parents=True, exist_ok=True)
                    new_video_stem = new_video_path.stem
                    for sub_path in matching_subs:
                        dest_sub = new_video_dir / f"{new_video_stem}{sub_path.suffix}"
                        shutil.copy2(sub_path, dest_sub)
                        logger.debug(f"   ✅ {sub_path.name} → {dest_sub}")
                        copied += 1

                if copied:
                    logger.debug(f"   已复制 {copied} 个字幕文件")
                else:
                    logger.debug(f"   未找到匹配的字幕文件")
            else:
                logger.debug(f"   无字幕文件")

        # ── NFO + images (shared with RSS flow via write_episode_files) ──
        meta = await generate_metadata_collection(tvshow, seasons, episodes, output_root)
        summary["nfoGenerated"] += meta["nfoGenerated"]
        summary["imagesDownloaded"] += meta["imagesDownloaded"]

        # ── Resume download ──
        logger.debug("▶️ 恢复种子下载...")
        await resume_torrent(client, info_hash)
        logger.debug("   ✅ 下载已恢复")

        logger.info("批量处理完成: NFO=%d 图片=%d 重命名文件=%d 输出目录=%s",
                    summary['nfoGenerated'], summary['imagesDownloaded'],
                    summary.get('filesRenamed', 0), output_root)
        logger.debug(f"   生成 NFO: {summary['nfoGenerated']} 个")
        logger.debug(f"   下载图片: {summary['imagesDownloaded']} 张")
        if summary["filesRenamed"]:
            logger.debug(f"   重命名文件: {summary['filesRenamed']} 个")
        logger.debug(f"   输出目录: {output_root}")

    except Exception as e:
        summary["error"] = str(e)
        logger.error(f"❌ 确认执行出错: {e}")

    return summary


# ═══════════════════════════════════════════════════════════════════════
# Backward-compatible wrapper (CLI + legacy API)
# ═══════════════════════════════════════════════════════════════════════

async def process_torrent(torrent_path: str) -> None:
    """Process a torrent file through the full pipeline (preview + confirm).

    Backward-compatible wrapper used by CLI mode (``--torrent``) and scan mode
    (``--scan``).
    """
    preview_data = await build_preview(torrent_path)
    await execute_confirm(preview_data)
