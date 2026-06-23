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

    If a single TMDB season contains more episodes than a Bangumi entry
    (e.g. TMDB S01 has 24 eps but Bangumi splits into two 12-ep entries),
    episodes beyond the current entry's ``eps`` automatically overflow to
    the next entry in the chain.
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

    # 3. Handle episode overflow across chain entries.
    #    TMDB may merge multiple Bangumi seasons into one season — when the
    #    episode number exceeds the current entry's ``eps``, carry the
    #    remainder to the next entry.
    remaining_ep = episode
    current_idx = target_idx

    while current_idx < len(chain):
        entry = chain[current_idx]
        eps = entry.get("eps", 0)
        name = entry.get("name_cn") or entry["name"]

        if eps > 0 and remaining_ep > eps:
            print(
                f"   TMDB S{season:02d}E{episode:02d} → "
                f"[{current_idx + 1}] {name} 仅有 {eps} 集，溢出到下一季"
            )
            remaining_ep -= eps
            current_idx += 1
            continue

        # This entry can hold the episode
        target_subject = chain[current_idx]
        print(
            f"   TMDB S{season:02d} → Bangumi [{current_idx + 1}] "
            f"{name} [id: {target_subject['id']}]"
        )
        print(f"   条目内集号: EP{remaining_ep}")

        return {"targetSubject": target_subject, "withinEpNum": remaining_ep}

    # Exhausted the chain without finding a suitable entry
    print(
        f"❌ 无法匹配：TMDB S{season:02d}E{episode:02d} "
        f"溢出了 Bangumi 链的所有条目"
    )
    _print_chain_for_debug(chain)
    return None


def _print_chain_for_debug(chain):
    print("   Bangumi 条目列表:")
    for i, s in enumerate(chain):
        name = s.get("name_cn") or s["name"]
        print(f"   [{i + 1}] {name} ({s.get('date', '?')}, {s.get('eps', 0)} 集)")
