"""Console output formatting utilities."""


def print_season_map(season_map: dict[int, dict]) -> None:
    """Print the TMDB Season → Episodes mapping table.

    Args:
        season_map: dict mapping season_number to {name, episodes: [{epNum, absOrder, ...}]}
    """
    sorted_seasons = sorted(season_map.items(), key=lambda x: x[0])
    for s_num, s_data in sorted_seasons:
        first = s_data["episodes"][0]
        last = s_data["episodes"][-1]
        abs_info = ""
        if first.get("absOrder"):
            abs_info = f" 绝对集号 {first['absOrder']}-{last['absOrder']}"
        print(f'   S{s_num}: {len(s_data["episodes"])} 集{abs_info} "{s_data["name"]}"')


def print_chain(chain: list[dict]) -> None:
    """Print the Bangumi entry chain.

    Args:
        chain: list of subject dicts with id, name, name_cn, date, eps
    """
    print("\n📋 Bangumi 条目链:")
    for i, s in enumerate(chain):
        name = s.get("name_cn") or s.get("name", "?")
        date = s.get("date", "?")
        eps = s.get("eps", 0)
        print(f"   [{i + 1}] {name} ({date}, {eps} 集) [id: {s['id']}]")


def print_result(opts: dict) -> None:
    """Print the full query result.

    Args:
        opts: dict with input, tvShow, detail, targetTmdbEp, targetSubject,
              targetBgmEp (optional), bgmEpisodes (optional)
    """
    inp = opts["input"]
    tv_show = opts["tvShow"]
    detail = opts["detail"]
    target_tmdb_ep = opts["targetTmdbEp"]
    target_subject = opts["targetSubject"]
    target_bgm_ep = opts.get("targetBgmEp")
    bgm_episodes = opts.get("bgmEpisodes")

    show_name = inp["showName"]
    season = inp["season"]
    episode = inp["episode"]

    print("\n" + "═" * 55)
    print("📺 查询结果")
    print("═" * 55)
    print(f"输入:        {show_name} S{season:02d}E{episode:02d}")
    print(f"TMDB 节目:   {tv_show['name']}")
    print(f"TMDB 原名:   {detail.get('original_name') or tv_show.get('original_name')}")
    print(f"TMDB 首播:   {detail.get('first_air_date') or tv_show.get('first_air_date', '未知')}")
    print(f'TMDB 剧集:   S{season}E{episode} - "{target_tmdb_ep["name"]}"')
    if target_tmdb_ep.get("absOrder"):
        print(f'绝对集号:    #{target_tmdb_ep["absOrder"]}')
    print("─" * 55)
    print(f"BGM 条目:    {target_subject.get('name_cn') or target_subject['name']}")
    print(f"BGM 原名:    {target_subject['name']}")
    print(f"BGM 日期:    {target_subject.get('date', '未知')}")
    print(f"BGM 条目 ID: {target_subject['id']}")
    print(f"BGM 条目 URL: https://bgm.tv/subject/{target_subject['id']}")

    if target_bgm_ep:
        ep_num = target_bgm_ep.get("ep") or target_bgm_ep.get("sort")
        print("─" * 55)
        print(f"BGM 集数:    EP{ep_num}")
        print(f"BGM 标题:    {target_bgm_ep.get('name_cn') or target_bgm_ep.get('name')}")
        print(f"BGM 原名:    {target_bgm_ep.get('name')}")
        print(f"BGM EP ID:   {target_bgm_ep['id']}")
        print(f"BGM EP URL:  https://bgm.tv/ep/{target_bgm_ep['id']}")
    else:
        print("─" * 55)
        print("⚠️ 未精确匹配到剧集（Bangumi 可能集数编号不同）")
        if bgm_episodes:
            first_ep = bgm_episodes[0]
            last_ep = bgm_episodes[-1]
            first_num = first_ep.get("ep") or first_ep.get("sort")
            last_num = last_ep.get("ep") or last_ep.get("sort")
            print(f"\n可用的剧集范围: EP{first_num} ~ EP{last_num}")
            print("前5集预览:")
            for ep in bgm_episodes[:5]:
                ep_num = ep.get("ep") or ep.get("sort")
                ep_name = ep.get("name_cn") or ep.get("name")
                print(f"   EP{ep_num}: {ep_name} [id: {ep['id']}]")
    print("═" * 55)


def print_nfo_generated(file_path: str) -> None:
    """Print NFO generation confirmation.

    Args:
        file_path: Path to the generated NFO file
    """
    print(f"\n📄 NFO 文件已生成: {file_path}")
