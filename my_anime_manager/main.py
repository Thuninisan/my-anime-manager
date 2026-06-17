"""CLI entry point — bridges Jellyfin, TMDB, Bangumi, and qBittorrent.

Three modes:
  Single episode:  python -m my_anime_manager "Show S01E12" [--nfo]
  Torrent batch:   python -m my_anime_manager --torrent <path>
  Scan directory:  python -m my_anime_manager --scan [dir]
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

from . import config
from .clients import bangumi as bgm_client
from .services import bangumi as bangumi_service
from .services import tmdb as tmdb_service
from .services.batch_service import process_torrent
from .services.image_downloader import (
    download_episode_thumb,
    download_season_poster,
    download_show_images,
)
from .services.mapper import find_target_entry
from .services.nfo_generator import (
    generate_episode_nfo,
    generate_season_nfo,
    generate_tv_show_nfo,
)
from .utils.formatter import (
    print_nfo_generated,
    print_result,
    print_season_map,
)
from .utils.parser import parse_input


def _sanitize_dir_name(name: str | None) -> str:
    """Remove illegal characters from a directory name."""
    if not name:
        return "unknown"
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


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


async def _single_episode_mode(show_name: str, season: int, episode: int, nfo_flag: bool) -> None:
    """Handle single episode query mode.

    Args:
        show_name: Show name from parsed input
        season: Season number
        episode: Episode number
        nfo_flag: Whether to generate NFO files and download images
    """
    print(f"🎯 查询: {show_name} S{season:02d}E{episode:02d}")
    print("─" * 55)

    # ---------- Step 1: TMDB search ----------
    print("\n📡 === TMDB 阶段 ===")
    tv_show = await tmdb_service.search_tv_show(show_name)
    if not tv_show:
        print(f"❌ TMDB 未找到《{show_name}》")
        sys.exit(1)

    # ---------- Step 2: TMDB details ----------
    detail = await tmdb_service.get_tv_show_detail(tv_show["id"])
    jp_name = detail.get("original_name") or tv_show["name"]
    print(f"   日文名: {jp_name}")
    print(f"   首播日期: {detail.get('first_air_date', '未知')}")
    print(
        f"   总季数: {detail.get('number_of_seasons', 0)}, "
        f"总集数: {detail.get('number_of_episodes', 0)}"
    )

    # ---------- Step 3: Build season→episodes map ----------
    print("\n📊 构建 TMDB 季→集映射（默认分季）...")
    season_map = await tmdb_service.build_season_episode_map(tv_show["id"])
    print_season_map(season_map)

    # Validate target season/episode
    if season not in season_map:
        sorted_keys = sorted(season_map.keys())
        print(f"\n❌ TMDB 中不存在第 {season} 季")
        print(f"   可用季: {', '.join(str(k) for k in sorted_keys)}")
        sys.exit(1)

    target_season_data = season_map[season]
    if episode < 1 or episode > len(target_season_data["episodes"]):
        print(
            f"\n❌ S{season} 只有 {len(target_season_data['episodes'])} 集，"
            f"EP{episode} 不存在"
        )
        sys.exit(1)

    target_tmdb_ep = target_season_data["episodes"][episode - 1]
    print(
        f'\n   🎬 TMDB 目标: S{season}E{episode} - '
        f'"{target_tmdb_ep["name"]}"'
    )
    if target_tmdb_ep.get("absOrder"):
        print(f'      绝对集号: #{target_tmdb_ep["absOrder"]}')

    # ---------- Step 4: Bangumi search ----------
    print("\n📚 === Bangumi 阶段 ===")

    # Search with Japanese name
    bgm_results = await bangumi_service.search_bangumi(jp_name)

    # Retry with Chinese name
    if not bgm_results:
        print(f'   用中文名重试: "{tv_show["name"]}"')
        bgm_results = await bangumi_service.search_bangumi(tv_show["name"])

    # Retry with TMDB name
    if (
        not bgm_results
        and detail.get("name") != jp_name
        and detail.get("name") != tv_show["name"]
    ):
        print(f'   用 TMDB 名称重试: "{detail["name"]}"')
        bgm_results = await bangumi_service.search_bangumi(detail["name"])

    if not bgm_results:
        print("❌ Bangumi 未找到该节目")
        sys.exit(1)

    # ---------- Step 5: Find first entry and traverse sequel chain ----------
    print("\n🔗 遍历 Bangumi 条目链...")

    first_result_id = bgm_results[0]["id"]
    initial_subject = await bgm_client.get_subject(first_result_id)
    init_name = initial_subject.get("name_cn") or initial_subject["name"]
    print(f"   搜索命中: {init_name} [id: {initial_subject['id']}]")

    first_id = await bangumi_service.find_first_in_chain(initial_subject["id"])
    print(f"   起始条目 ID: {first_id}")

    # Traverse sequels
    chain = await bangumi_service.build_bangumi_chain(first_id)

    # If chain is too short, try date-sorted fallback
    total_tv_seasons = len(season_map)
    if len(chain) < total_tv_seasons:
        print(
            f"\n⚠️ 续集链只有 {len(chain)} 个条目，"
            f"但 TMDB 有 {total_tv_seasons} 季"
        )
        print("   尝试按日期排序的备选方案...")
        chain = await bangumi_service.build_chain_by_date(bgm_results)

        if len(chain) < total_tv_seasons:
            print(
                f"\n⚠️ 备选方案也只有 {len(chain)} 个条目，"
                f"仍少于 TMDB 季数"
            )
            print("   将尽力匹配...")

    if not chain:
        print("❌ 未找到任何有效的 Bangumi 条目")
        sys.exit(1)

    # ---------- Step 6: Map to Bangumi entry ----------
    print("\n🔗 匹配 Bangumi 条目...")

    start_entry_id = _find_entry_in_chain(jp_name, chain)
    print(f"   🎯 匹配条目: id={start_entry_id}")

    mapping = find_target_entry(
        chain,
        start_entry_id=start_entry_id,
        season=season,
        episode=episode,
    )

    if not mapping:
        sys.exit(1)

    target_subject = mapping["targetSubject"]
    within_ep_num = mapping["withinEpNum"]

    # ---------- Step 7: Get Bangumi episodes ----------
    print(f"\n📡 获取 Bangumi 剧集列表 (subject_id={target_subject['id']})...")
    bgm_episodes = await bgm_client.get_episodes(target_subject["id"])
    print(f"   → {len(bgm_episodes)} 集 (本篇)")

    # ---------- Step 8: Match episode ----------
    target_bgm_ep = bangumi_service.match_episode(bgm_episodes, within_ep_num)

    # ---------- Output result ----------
    print_result({
        "input": {"showName": show_name, "season": season, "episode": episode},
        "tvShow": tv_show,
        "detail": detail,
        "targetTmdbEp": target_tmdb_ep,
        "targetSubject": target_subject,
        "targetBgmEp": target_bgm_ep,
        "bgmEpisodes": bgm_episodes,
    })

    # ---------- Step 9: Generate NFO + images (optional) ----------
    if nfo_flag:
        season_number = next(
            (i + 1 for i, e in enumerate(chain) if e["id"] == target_subject["id"]),
            1,
        )
        episode_number = (target_bgm_ep.get("sort") if target_bgm_ep else None) or within_ep_num
        bangumi_ep_id = target_bgm_ep.get("id") if target_bgm_ep else None
        original_name = (
            detail.get("original_name")
            or tv_show.get("original_name")
            or tv_show["name"]
        )
        bangumi_subject_name = target_subject.get("name_cn") or target_subject["name"]

        print("\n📄 生成 NFO 文件...")
        print(f"   季号 (chain 位置): {season_number}")
        print(f"   集号 (Bangumi sort): {episode_number}")
        print(f"   Bangumi EP ID: {bangumi_ep_id or '无'}")

        season_dir = f"Season {season_number}"
        earliest = min(chain, key=lambda e: e.get("date") or "9999-99-99")
        root_dir = _sanitize_dir_name(
            earliest.get("name_cn") or earliest["name"]
        )
        output_dir = str(Path(root_dir) / season_dir)
        episode_str = f"{episode_number:02d}"
        nfo_base_name = f"{_sanitize_dir_name(bangumi_subject_name)} {episode_str}"

        # Episode thumbnail (download first, NFO references filename)
        print("\n🖼️ 下载图片...")
        thumb_full_path = await download_episode_thumb(
            target_tmdb_ep.get("stillPath", ""), output_dir, nfo_base_name
        )
        if thumb_full_path:
            print(f"   ✅ 剧集缩略图: {thumb_full_path}")
        thumb_filename = Path(thumb_full_path).name if thumb_full_path else ""

        # Episode NFO
        nfo_path = generate_episode_nfo(
            tmdb_show_name=tv_show["name"],
            tmdb_original_name=original_name,
            tmdb_ep_name=target_tmdb_ep["name"],
            tmdb_ep_overview=target_tmdb_ep.get("overview", ""),
            tmdb_ep_air_date=target_tmdb_ep.get("airDate", ""),
            tmdb_ep_runtime=target_tmdb_ep.get("runtime", 0),
            tmdb_ep_id=target_tmdb_ep["tmdbId"],
            season_number=season_number,
            episode_number=episode_number,
            bangumi_ep_id=bangumi_ep_id,
            bangumi_subject_name=bangumi_subject_name,
            directors=target_tmdb_ep.get("directors", []),
            writers=target_tmdb_ep.get("writers", []),
            actors=target_tmdb_ep.get("guestStars", []),
            thumb_path=thumb_filename,
            studios=detail.get("studios", []),
            output_dir=output_dir,
        )
        print_nfo_generated(nfo_path)

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
            output_dir=root_dir,
        )
        print(f"   ✅ tvshow.nfo: {tvshow_nfo_path}")

        # season.nfo (Bangumi data)
        try:
            full_subject = await bgm_client.get_subject(target_subject["id"])
            season_nfo_path = generate_season_nfo(
                title=full_subject.get("name_cn") or full_subject["name"],
                original_title=full_subject["name"],
                plot=full_subject.get("summary", ""),
                premiered=full_subject.get("date", ""),
                season_number=season_number,
                bangumi_id=full_subject["id"],
                output_dir=output_dir,
            )
            print(f"   ✅ season.nfo: {season_nfo_path}")

            # Season poster → at show level
            poster_path = await download_season_poster(
                full_subject, root_dir, season_number
            )
            if poster_path:
                print(f"   ✅ 季海报: {poster_path}")
        except Exception as e:
            print(f"   ⚠️ 季数据获取失败: {e}")

        # Show-level images
        await download_show_images(tvshow_tmdb_id, root_dir)


async def _scan_and_process(dir_path: str) -> None:
    """Scan a directory for .torrent files and process each one.

    Args:
        dir_path: Directory path to scan
    """
    abs_dir = Path(dir_path).resolve()
    if not abs_dir.exists():
        print(f"❌ 目录不存在: {abs_dir}")
        sys.exit(1)

    files = sorted(abs_dir.glob("*.torrent"))

    if not files:
        print(f"📭 {abs_dir} 中没有 .torrent 文件")
        return

    print(f"📁 扫描到 {len(files)} 个 torrent 文件\n")

    processed = 0
    deleted = 0
    failed = 0

    for file in files:
        print("═" * 55)
        print(f"📦 处理: {file.name}")
        print("═" * 55)

        try:
            await process_torrent(str(file))
            # Delete on success
            file.unlink()
            print(f"\n🗑️ 已删除: {file.name}")
            processed += 1
            deleted += 1
        except Exception as e:
            print(f"\n❌ 处理失败: {file.name} — {e}")
            failed += 1
        print("")

    print("═" * 55)
    print("📊 扫描完成")
    print(f"   处理: {processed} 个")
    print(f"   删除: {deleted} 个")
    print(f"   失败: {failed} 个")
    print("═" * 55)


def main() -> None:
    """Parse CLI arguments and route to the appropriate mode."""
    parser = argparse.ArgumentParser(
        description="TMDB + Bangumi 联动工具，为 Jellyfin 生成 NFO 文件，"
                    "支持 qBittorrent 下载管理",
    )
    parser.add_argument(
        "input",
        nargs="?",
        help='节目名 SXXEXX（如 "葬送のフリーレン S01E12"）',
    )
    parser.add_argument(
        "--nfo",
        action="store_true",
        help="生成 NFO 文件并下载图片",
    )
    parser.add_argument(
        "--torrent",
        metavar="PATH",
        help="处理单个 torrent 文件",
    )
    parser.add_argument(
        "--scan",
        nargs="?",
        const=config.TORRENT_WATCH_DIR,
        metavar="DIR",
        help=f"扫描目录下的 .torrent 文件 (默认: {config.TORRENT_WATCH_DIR})",
    )
    parser.add_argument(
        "--serve",
        nargs="?",
        const="0.0.0.0:8000",
        metavar="HOST:PORT",
        help="启动 API 服务 (默认: 0.0.0.0:8000)",
    )

    args = parser.parse_args()

    # Serve mode
    if args.serve is not None:
        host, _, port_str = args.serve.partition(":")
        port = int(port_str) if port_str else 8000
        import uvicorn
        print(f"🚀 API 服务启动: http://{host}:{port}")
        print(f"   📖 文档: http://{host}:{port}/docs")
        uvicorn.run(
            "my_anime_manager.api:app",
            host=host,
            port=port,
        )
        return

    # Torrent mode
    if args.torrent:
        torrent_path = Path(args.torrent)
        if not torrent_path.exists():
            print(f"❌ 文件不存在: {args.torrent}")
            sys.exit(1)
        asyncio.run(process_torrent(str(torrent_path)))
        return

    # Scan mode
    if args.scan is not None:
        scan_dir = args.scan or config.TORRENT_WATCH_DIR
        if not scan_dir:
            print(
                "❌ 请指定扫描目录，或在环境变量中设置 TORRENT_WATCH_DIR"
            )
            print("   用法: python -m my_anime_manager --scan [path]")
            sys.exit(1)
        asyncio.run(_scan_and_process(scan_dir))
        return

    # Single episode mode
    if not args.input:
        parser.print_help()
        print("\n示例:")
        print('  python -m my_anime_manager "葬送のフリーレン S01E12"')
        print('  python -m my_anime_manager "我推的孩子 S02E12" --nfo')
        print("  python -m my_anime_manager --torrent xxx.torrent")
        print("  python -m my_anime_manager --scan")
        print("  python -m my_anime_manager --serve")
        sys.exit(1)

    # Parse input
    parsed = parse_input(args.input)
    if not parsed:
        print(f'❌ 输入格式错误: "{args.input}"')
        print(
            '   请使用格式: 节目名 SXXEXX（如 "葬送のフリーレン S01E12"）'
        )
        sys.exit(1)

    asyncio.run(
        _single_episode_mode(
            parsed["showName"],
            parsed["season"],
            parsed["episode"],
            args.nfo,
        )
    )


def main_entry() -> None:
    """Console script entry point (called by `anime-manager` command)."""
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断")
        sys.exit(130)
    except Exception as err:
        print("\n💥 运行出错:")
        if hasattr(err, "response"):
            resp = err.response  # type: ignore[attr-defined]
            print(
                f"   HTTP {resp.status_code}: "
                f"{str(resp.text)[:500]}"
            )
        elif hasattr(err, "request"):
            print(f"   网络错误: {err}")
        else:
            print(f"   {err}")
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main_entry()
