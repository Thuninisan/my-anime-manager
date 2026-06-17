"""Batch torrent processing orchestration with qBittorrent."""

import re
from pathlib import Path

from ..clients import bangumi as bgm_client
from ..clients.qbittorrent import (
    login as qb_login,
    add_torrent,
    get_torrent_files,
    rename_file,
    resume_torrent,
)
from .. import config
from . import tmdb as tmdb_service
from . import bangumi as bangumi_service
from .image_downloader import (
    download_episode_thumb,
    download_season_poster,
    download_show_images,
)
from .mapper import find_target_entry
from .nfo_generator import (
    generate_episode_nfo,
    generate_season_nfo,
    generate_tv_show_nfo,
)
from ..utils.torrent_parser import parse_qbit_file_list


def _sanitize_dir_name(name: str | None) -> str:
    """Remove illegal characters from a directory name."""
    if not name:
        return "unknown"
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


def _pick_show_name(file_list: list[dict]) -> tuple[str, str | None]:
    """Pick the show name from the parsed file list.

    Uses majority vote among parsed titles, then strips trailing year
    numbers that anitopy often leaves attached (e.g. "Horimiya -piece- 2023").

    Args:
        file_list: List of dicts with 'showName' keys

    Returns:
        Tuple of (best show name string, extracted year or None)
    """
    names = [f["showName"] for f in file_list if f.get("showName")]
    if not names:
        return "", None

    # Majority vote
    from collections import Counter
    best = Counter(names).most_common(1)[0][0]

    # Extract trailing year before stripping (e.g. "Horimiya 2021" → year="2021")
    year_match = re.search(r"[\s\-–—]*(\d{4})$", best)
    year = year_match.group(1) if year_match else None

    # Strip trailing year (e.g. "Horimiya -piece- 2023" → "Horimiya -piece-")
    best = re.sub(r"[\s\-–—]*\d{4}$", "", best).strip()
    return best, year


def _find_entry_in_chain(target_title: str, chain: list[dict]) -> int:
    """Find the chain entry whose name best matches the target title.

    Searches chain entries (which have full name/name_cn from get_subject),
    not the limited search API results.
    """
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


async def process_torrent(torrent_path: str) -> None:
    """Process a torrent file through the full pipeline.

    Steps:
    1. Login to qBittorrent
    2. Add torrent in paused state
    3. Get file list from qBittorrent
    4. Parse files into episodes/extras
    5. Determine show name, search TMDB
    6. Get TMDB details + season map
    7. Search Bangumi + build entry chain
    8. Preload Bangumi data + season posters/NFOs
    9. Process each episode: match, NFO, thumb, rename map
    10. Rename files in qBittorrent
    11. Resume download

    Args:
        torrent_path: Path to .torrent file
    """
    torrent_name = Path(torrent_path).stem

    # ---------- Step 1: Login to qBittorrent ----------
    print("🔗 连接 qBittorrent...")
    try:
        client = await qb_login(
            config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD
        )
        print("   ✅ qBittorrent 登录成功\n")
    except Exception as e:
        print(f"❌ qBittorrent 登录失败: {e}")
        return

    # ---------- Step 2: Add torrent (paused) ----------
    print("📥 添加种子到 qBittorrent（暂停）...")
    try:
        info_hash = await add_torrent(
            client, torrent_path,
            config.QBITTORRENT_SAVE_PATH, torrent_name,
        )
        print("   ✅ 种子已添加")
        print(f"   Info hash: {info_hash}")
        print(f"   保存路径: {config.QBITTORRENT_SAVE_PATH}\n")
    except Exception as e:
        print(f"❌ 添加种子失败: {e}")
        return

    # ---------- Step 3: Get file list ----------
    print("📋 获取种子文件列表...")
    try:
        file_list = await get_torrent_files(client, info_hash)
        print(f"   → {len(file_list)} 个文件\n")
    except Exception as e:
        print(f"❌ 获取文件列表失败: {e}")
        return

    # ---------- Step 4: Parse file list ----------
    result = parse_qbit_file_list(file_list, torrent_name)
    episodes = result["episodes"]
    extras = result["extras"]
    if not episodes:
        print("❌ 没有找到可处理的剧集文件")
        return

    # Sort by season+episode
    episodes.sort(key=lambda e: (e["season"], e["episode"]))

    # ---------- Step 5: Determine show name, search TMDB ----------
    show_name, show_year = _pick_show_name(episodes)
    print(f'🔍 从文件名推断节目名: "{show_name}"')
    if show_year:
        print(f'   从文件名提取年份: {show_year}')
    print()

    print("📡 === TMDB 阶段 ===")
    tv_show = await tmdb_service.search_tv_show(show_name, prefer_year=show_year)

    # Retry with first file's full show name
    if not tv_show and episodes[0]["showName"] != show_name:
        print(f'   用完整文件名重试: "{episodes[0]["showName"]}"')
        tv_show = await tmdb_service.search_tv_show(
            episodes[0]["showName"], prefer_year=show_year
        )

    if not tv_show:
        print(f'❌ TMDB 未找到节目，尝试的名称: "{show_name}"')
        return

    # ---------- Step 6: TMDB details + season map ----------
    detail = await tmdb_service.get_tv_show_detail(tv_show["id"])
    jp_name = detail.get("original_name") or tv_show["name"]
    original_name = (
        detail.get("original_name")
        or tv_show.get("original_name")
        or tv_show["name"]
    )
    print(f"   日文原名: {jp_name}")
    print(f"   首播日期: {detail.get('first_air_date', '未知')}")

    print("\n📊 构建 TMDB 季→集映射...")
    season_map = await tmdb_service.build_season_episode_map(tv_show["id"])

    # ---------- Step 7: Bangumi search + build chain ----------
    print("\n📚 === Bangumi 阶段 ===")
    bgm_results = await bangumi_service.search_bangumi(jp_name)
    if not bgm_results:
        print(f'   用中文名重试: "{tv_show["name"]}"')
        bgm_results = await bangumi_service.search_bangumi(tv_show["name"])
    if (
        not bgm_results
        and detail.get("name") != jp_name
        and detail.get("name") != tv_show["name"]
    ):
        print(f'   用 TMDB 名称重试: "{detail["name"]}"')
        bgm_results = await bangumi_service.search_bangumi(detail["name"])
    if not bgm_results:
        print("❌ Bangumi 未找到该节目")
        return

    print("\n🔗 遍历 Bangumi 条目链...")
    first_result_id = bgm_results[0]["id"]
    initial_subject = await bgm_client.get_subject(first_result_id)
    init_name = initial_subject.get("name_cn") or initial_subject["name"]
    print(f"   搜索命中: {init_name} [id: {initial_subject['id']}]")

    first_id = await bangumi_service.find_first_in_chain(initial_subject["id"])
    print(f"   起始条目 ID: {first_id}")

    chain = await bangumi_service.build_bangumi_chain(first_id)
    if len(chain) < len(season_map):
        print(
            f"\n⚠️ 续集链只有 {len(chain)} 个条目，"
            f"但 TMDB 有 {len(season_map)} 季"
        )
        print("   尝试按日期排序的备选方案...")
        chain = await bangumi_service.build_chain_by_date(bgm_results)
    if not chain:
        print("❌ 未找到任何有效的 Bangumi 条目")
        return

    start_entry_id = _find_entry_in_chain(jp_name, chain)
    print(f"   🎯 匹配条目: id={start_entry_id}")

    # Folder name = earliest chain entry by air date (Chinese name preferred)
    earliest = min(chain, key=lambda e: e.get("date") or "9999-99-99")
    show_dir_name = _sanitize_dir_name(
        earliest.get("name_cn") or earliest["name"]
    )
    output_root = str(Path(config.QBITTORRENT_SAVE_PATH) / show_dir_name)

    # Re-search TMDB with the Bangumi-derived show name for show-level metadata
    print("\n📡 用 Bangumi 名称重新查找 TMDB 节目级数据...")
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
        print(f"   ✅ 命中: {tvshow_title} [TMDB id: {tvshow_tmdb_id}]")
    else:
        print(f"   ⚠️ 未命中，使用原始搜索结果")
        tvshow_detail = detail
        tvshow_title = tv_show["name"]
        tvshow_original = original_name
        tvshow_tmdb_id = tv_show["id"]

    # tvshow.nfo (TMDB data, from Bangumi-name search)
    tvshow_nfo_path = generate_tv_show_nfo(
        title=tvshow_title,
        original_title=tvshow_original,
        plot=tvshow_detail.get("overview", ""),
        premiered=tvshow_detail.get("first_air_date", ""),
        tmdb_id=tvshow_tmdb_id,
        genres=tvshow_detail.get("genres", []),
        studios=tvshow_detail.get("studios", []),
        rating=tvshow_detail.get("vote_average", 0),
        status=tvshow_detail.get("status", ""),
        output_dir=output_root,
    )
    print(f"   ✅ tvshow.nfo: {tvshow_nfo_path}")

    # Show-level images
    print("\n🖼️ 下载节目图片...")
    await download_show_images(tvshow_tmdb_id, output_root)



    # ---------- Step 8: Preload Bangumi data for needed seasons ----------
    print("\n📡 预加载 Bangumi 剧集列表 + 季海报...")
    bgm_episode_cache: dict[int, list[dict]] = {}

    # Determine which chain indices have files
    file_seasons = {f["season"] for f in episodes}
    start_idx = next(
        (i for i, e in enumerate(chain) if e["id"] == start_entry_id), 0
    )
    needed_indices = {start_idx + (s - 1) for s in file_seasons}

    for ci in sorted(needed_indices):
        entry = chain[ci]
        season_number = ci + 1
        season_dir = str(Path(output_root) / f"Season {season_number}")

        # Full subject data (for season.nfo)
        full_subject = None
        try:
            full_subject = await bgm_client.get_subject(entry["id"])
        except Exception as e:
            entry_name = entry.get("name_cn") or entry["name"]
            print(f"   ⚠️ 获取 {entry_name} 完整数据失败: {e}")

        # Season poster → at show level, next to Season folders
        if full_subject and full_subject.get("images"):
            poster_path = await download_season_poster(
                full_subject, output_root, season_number
            )
            if poster_path:
                print(f"   🖼️ Season {season_number} poster → {poster_path}")

        # season.nfo (Bangumi data) → inside Season folder
        if full_subject:
            season_nfo_path = generate_season_nfo(
                title=full_subject.get("name_cn") or full_subject["name"],
                original_title=full_subject["name"],
                plot=full_subject.get("summary", ""),
                premiered=full_subject.get("date", ""),
                season_number=season_number,
                bangumi_id=full_subject["id"],
                output_dir=season_dir,
            )
            print(f"   📄 Season {season_number} nfo → {season_nfo_path}")

        # Episode list
        if entry.get("eps", 0) > 0:
            try:
                eps = await bgm_client.get_episodes(entry["id"])
                bgm_episode_cache[entry["id"]] = eps
                entry_name = entry.get("name_cn") or entry["name"]
                print(f"   [{entry_name}] → {len(eps)} 集")
            except Exception as e:
                entry_name = entry.get("name_cn") or entry["name"]
                print(f"   ⚠️ 获取 {entry_name} 剧集失败: {e}")

    # ---------- Step 9: Process each episode ----------
    print(f"\n📄 处理剧集 (共 {len(episodes)} 个)...\n")

    generated = 0
    skipped = 0
    all_file_mappings: list[dict] = []

    for file in episodes:
        tmdb_season = file["season"]
        tmdb_ep_num = file["episode"]
        old_torrent_path = file["torrentPath"]  # Note: camelCase from torrent_parser
        filename = file["fileName"]

        # 9a. Find TMDB episode metadata
        season_data = season_map.get(tmdb_season)
        if not season_data:
            print(f"   ⚠️ 跳过 {filename}: TMDB 中无 Season {tmdb_season}")
            skipped += 1
            continue
        if tmdb_ep_num < 1 or tmdb_ep_num > len(season_data["episodes"]):
            print(
                f"   ⚠️ 跳过 {filename}: S{tmdb_season} 只有 "
                f"{len(season_data['episodes'])} 集"
            )
            skipped += 1
            continue
        tmdb_ep = season_data["episodes"][tmdb_ep_num - 1]

        # 9b. Map to Bangumi entry
        mapping = find_target_entry(
            chain,
            start_entry_id=start_entry_id,
            season=tmdb_season,
            episode=tmdb_ep_num,
        )
        if not mapping:
            print(f"   ⚠️ 跳过 {filename}: 无法匹配 Bangumi 条目")
            skipped += 1
            continue
        target_subject = mapping["targetSubject"]
        within_ep_num = mapping["withinEpNum"]

        # 9c. Season number (position in chain)
        season_number = next(
            (i + 1 for i, e in enumerate(chain) if e["id"] == target_subject["id"]),
            1,
        )

        # 9d. Match Bangumi episode
        bgm_eps = bgm_episode_cache.get(target_subject["id"], [])
        bgm_ep = bangumi_service.match_episode(bgm_eps, within_ep_num)
        episode_number = (bgm_ep.get("sort") if bgm_ep else None) or within_ep_num
        bangumi_ep_id = bgm_ep.get("id") if bgm_ep else None
        bangumi_subject_name = target_subject.get("name_cn") or target_subject["name"]

        # 9e. Download episode thumbnail
        season_dir = f"Season {season_number}"
        output_dir = str(Path(output_root) / season_dir)
        episode_str = f"{episode_number:02d}"
        nfo_base_name = f"{_sanitize_dir_name(bangumi_subject_name)} {episode_str}"
        thumb_full_path = await download_episode_thumb(
            tmdb_ep.get("stillPath", ""), output_dir, nfo_base_name
        )
        thumb_filename = Path(thumb_full_path).name if thumb_full_path else ""

        # 9f. Generate episode NFO
        nfo_path = generate_episode_nfo(
            tmdb_show_name=tv_show["name"],
            tmdb_original_name=original_name,
            tmdb_ep_name=tmdb_ep["name"],
            tmdb_ep_overview=tmdb_ep.get("overview", ""),
            tmdb_ep_air_date=tmdb_ep.get("airDate", ""),
            tmdb_ep_runtime=tmdb_ep.get("runtime", 0),
            tmdb_ep_id=tmdb_ep["tmdbId"],
            season_number=season_number,
            episode_number=episode_number,
            bangumi_ep_id=bangumi_ep_id,
            bangumi_subject_name=bangumi_subject_name,
            directors=tmdb_ep.get("directors", []),
            writers=tmdb_ep.get("writers", []),
            actors=tmdb_ep.get("guestStars", []),
            thumb_path=thumb_filename,
            studios=detail.get("studios", []),
            output_dir=output_dir,
        )

        print(f"   ✅ {filename}")
        print(f"      → {nfo_path}")
        if thumb_full_path:
            print(f"      → {thumb_full_path}")
        print(
            f"      S{season_number:02d}E{episode_number:02d} | "
            f"BGM epid: {bangumi_ep_id or '无'}"
        )
        generated += 1

        # Build qBittorrent rename mapping
        ext = Path(filename).suffix
        new_file_path = (
            f"{show_dir_name}/Season {season_number}/"
            f"{_sanitize_dir_name(bangumi_subject_name)} {episode_str}{ext}"
        )
        all_file_mappings.append({
            "oldPath": old_torrent_path,
            "newPath": new_file_path,
        })

    # Extra files → Season X/Extra/original-name.ext
    # Determine dominant season from episode counts
    season_counts: dict[int, int] = {}
    for ep in episodes:
        s = ep["season"]
        season_counts[s] = season_counts.get(s, 0) + 1
    dominant_season = (
        max(season_counts.items(), key=lambda x: x[1])[0]
        if season_counts
        else 1
    )

    for extra in extras:
        new_file_path = (
            f"{show_dir_name}/Season {dominant_season}/Extra/{extra['fileName']}"
        )
        all_file_mappings.append({
            "oldPath": extra["torrentPath"],
            "newPath": new_file_path,
        })

    # ---------- Step 10: Rename files in qBittorrent ----------
    if all_file_mappings:
        print(
            f"\n📝 在 qBittorrent 中重组文件结构 "
            f"({len(all_file_mappings)} 个文件)..."
        )
        renamed = 0
        for mapping in all_file_mappings:
            try:
                ok = await rename_file(
                    client,
                    info_hash,
                    mapping["oldPath"],
                    mapping["newPath"],
                )
                if ok:
                    print(
                        f"   ✅ {mapping['oldPath']} → {mapping['newPath']}"
                    )
                    renamed += 1
            except Exception as e:
                print(
                    f"   ⚠️ 重命名失败: {mapping['oldPath']} → "
                    f"{mapping['newPath']} — {e}"
                )
        print(f"   已完成 {renamed}/{len(all_file_mappings)} 个")

    # ---------- Step 11: Resume download ----------
    print("\n▶️ 恢复种子下载...")
    try:
        await resume_torrent(client, info_hash)
        print("   ✅ 下载已恢复")
    except Exception as e:
        print(f"   ⚠️ 恢复下载失败: {e}")

    # ---------- Step 12: Summary ----------
    print("\n" + "═" * 55)
    print("📊 批量处理完成")
    print(f"   生成 NFO: {generated} 个")
    if skipped > 0:
        print(f"   跳过: {skipped} 个")
    print(f"   输出目录: {output_root}")
    print(f"   qBittorrent: {config.QBITTORRENT_URL}")
    print("═" * 55)
