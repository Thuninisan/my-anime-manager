"""Episode mapping — file season/episode → Bangumi chain entry."""


def find_target_entry(
    chain: list[dict],
    start_entry_id: int | None,
    season: int,
    episode: int,
) -> dict | None:
    """Find the Bangumi entry and within-episode number for a file.

    Args:
        chain: Full Bangumi entry chain (from prequel to latest sequel).
        start_entry_id: Bangumi subject ID that matches TMDB's original title.
                        This is where counting starts (S01 = this entry).
        season: Season number from the file (1-based).
        episode: Episode number from the file (1-based).

    Returns:
        dict with targetSubject and withinEpNum keys, or None.
    """
    if not chain:
        return None

    # 1. Find starting position in chain
    start_idx = 0
    if start_entry_id:
        for i, entry in enumerate(chain):
            if entry["id"] == start_entry_id:
                start_idx = i
                break

    # 2. From start_idx, walk forward counting seasons.
    #    S01 = start_idx itself. S02 = next entry. etc.
    target_idx = start_idx + (season - 1)

    if target_idx >= len(chain):
        print(
            f"❌ 无法匹配：Bangumi 链只有 {len(chain)} 个条目，"
            f"起点 [{start_idx + 1}]，请求第 {season} 季"
        )
        _print_chain_for_debug(chain)
        return None

    target_subject = chain[target_idx]
    name = target_subject.get("name_cn") or target_subject["name"]
    print(
        f"   TMDB S{season:02d} → Bangumi [{target_idx + 1}] "
        f"{name} [id: {target_subject['id']}]"
    )

    # 3. Episode number within the entry
    within_ep_num = episode
    print(f"   条目内集号: EP{within_ep_num}")

    return {"targetSubject": target_subject, "withinEpNum": within_ep_num}


def _print_chain_for_debug(chain):
    print("   Bangumi 条目列表:")
    for i, s in enumerate(chain):
        name = s.get("name_cn") or s["name"]
        print(f"   [{i + 1}] {name} ({s.get('date', '?')}, {s.get('eps', 0)} 集)")
