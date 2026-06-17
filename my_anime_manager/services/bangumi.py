"""Bangumi business logic layer."""

from ..clients import bangumi as bgm_client


async def search_bangumi(keyword: str) -> list[dict]:
    """Search Bangumi for a keyword (wrapper with logging).

    Args:
        keyword: Search keyword

    Returns:
        List of search result dicts
    """
    print(f'🔍 Bangumi 搜索: "{keyword}"')
    data = await bgm_client.search_subjects(keyword)
    print(f"   → {len(data)} 个结果")
    return data


def is_skippable_subject(subject: dict) -> bool:
    """Check if a Bangumi subject should be skipped.

    Skips: movies, OVAs, OADs, recaps, specials.

    Args:
        subject: Bangumi subject dict

    Returns:
        True if the subject should be skipped
    """
    name_str = (
        (subject.get("name") or "")
        + " "
        + (subject.get("name_cn") or "")
    ).lower()

    skip_patterns = [
        "剧场版", "劇場版", "总集篇", "總集篇",
        "ova", "oad", "movie", "film",
        "特别篇", "特番", "総集編", "スペシャル",
        "映像特典", "未放送", "event",
    ]

    for pattern in skip_patterns:
        if pattern in name_str:
            return True

    # type 1 = book / movie
    if subject.get("type") == 1:
        return True
    if subject.get("platform") and "movie" in str(subject["platform"]).lower():
        return True

    return False


async def find_first_in_chain(subject_id: int) -> int:
    """Traverse prequel relations backward to find the first entry in a series.

    Args:
        subject_id: Starting Bangumi subject ID

    Returns:
        ID of the first subject in the chain
    """
    visited: set[int] = set()
    current_id = subject_id

    for _ in range(30):  # prevent infinite loop
        visited.add(current_id)
        relations = await bgm_client.get_relations(current_id)

        # Find prequel
        prequel = next(
            (r for r in relations if r.get("relation") == "前传"), None
        )

        if not prequel or prequel["id"] in visited:
            break
        print(
            f"   🔗 回溯前传: {prequel.get('name_cn') or prequel['name']} "
            f"(id: {prequel['id']})"
        )
        current_id = prequel["id"]

    return current_id


async def build_bangumi_chain(first_subject_id: int) -> list[dict]:
    """Build a chain of Bangumi entries by traversing sequel relations.

    Filters out non-TV entries (movies, OVAs, etc.).

    Args:
        first_subject_id: Starting subject ID (should be the first in series)

    Returns:
        List of chain entry dicts
    """
    chain: list[dict] = []
    visited: set[int] = set()
    current_id = first_subject_id

    for _ in range(30):
        if current_id in visited:
            print("   ⚠️ 检测到循环，停止遍历")
            break
        visited.add(current_id)

        try:
            subject = await bgm_client.get_subject(current_id)
        except Exception as e:
            print(f"   ❌ 获取条目 {current_id} 失败: {e}")
            break

        if is_skippable_subject(subject):
            print(
                f"   ⏩ 跳过: {subject.get('name_cn') or subject['name']} "
                f"[type={subject.get('type')}] (剧场版/OVA/总集篇)"
            )
        else:
            chain.append({
                "id": subject["id"],
                "name": subject["name"],
                "name_cn": subject.get("name_cn"),
                "date": subject.get("date"),
                "eps": subject.get("eps") or subject.get("total_episodes") or 0,
                "type": subject.get("type"),
                "platform": subject.get("platform"),
            })
            eps = chain[-1]["eps"]
            name = chain[-1]["name_cn"] or chain[-1]["name"]
            date = chain[-1].get("date", "未知日期")
            print(
                f"   📺 [{len(chain)}] {name} ({date}, {eps} 集) "
                f"[id: {subject['id']}]"
            )

        # Find sequel
        relations = await bgm_client.get_relations(current_id)
        sequel = next(
            (r for r in relations if r.get("relation") == "续集"), None
        )

        if not sequel or sequel["id"] in visited:
            print("   🔚 已达末项" if sequel else "   🔚 无更多续集")
            break
        current_id = sequel["id"]

    return chain


async def build_chain_by_date(bgm_search_results: list[dict]) -> list[dict]:
    """Fallback: build chain by sorting all search results by date.

    Used when the sequel chain is too short compared to TMDB seasons.

    Args:
        bgm_search_results: Raw Bangumi search results

    Returns:
        Deduplicated chain sorted by date
    """
    print("   📡 按日期排序构建条目链（备选方案）...")
    subjects = []

    for result in bgm_search_results[:15]:
        try:
            subject = await bgm_client.get_subject(result["id"])
            if not is_skippable_subject(subject):
                subjects.append({
                    "id": subject["id"],
                    "name": subject["name"],
                    "name_cn": subject.get("name_cn"),
                    "date": subject.get("date"),
                    "eps": subject.get("eps") or subject.get("total_episodes") or 0,
                    "type": subject.get("type"),
                    "platform": subject.get("platform"),
                })
        except Exception:
            pass

    # Sort by date
    subjects.sort(key=lambda s: s.get("date") or "9999-99-99")

    # Dedupe by name
    seen: set[str] = set()
    deduped = []
    for s in subjects:
        key = s.get("name_cn") or s["name"]
        if key not in seen:
            seen.add(key)
            deduped.append(s)

    print("   按日期排序后的条目:")
    for i, s in enumerate(deduped):
        name = s.get("name_cn") or s["name"]
        date = s.get("date", "?")
        eps = s.get("eps", 0)
        print(f"   [{i + 1}] {name} ({date}, {eps} 集) [id: {s['id']}]")

    return deduped


async def get_episode_count(
    subject_id: int, cached_subject: dict | None = None
) -> int:
    """Get the actual main-story episode count for a Bangumi subject.

    Prefers the subject's eps field. Falls back to episodes API total.

    Args:
        subject_id: Bangumi subject ID
        cached_subject: Optionally cached subject data

    Returns:
        Episode count, or 0 if unavailable
    """
    eps_from_wiki = (cached_subject or {}).get("eps") or \
                    (cached_subject or {}).get("total_episodes") or 0
    if eps_from_wiki > 0:
        return eps_from_wiki

    # If wiki eps=0 (unaired/unlisted), try episodes API
    total = await bgm_client.get_episode_total(subject_id)
    if total > 0:
        return total

    return 0


def match_episode(episodes: list[dict], target_ep_num: int) -> dict | None:
    """Match a target episode number in a Bangumi episode list.

    Three-tier fallback:
    1. Exact match on 'ep' field
    2. Match on 'sort' field
    3. Array index (0-based → 1-based)

    Args:
        episodes: Sorted Bangumi episode list
        target_ep_num: Target episode number

    Returns:
        Matched episode dict or None
    """
    # Prefer exact ep field match
    match = next((ep for ep in episodes if ep.get("ep") == target_ep_num), None)
    if match:
        return match

    # Try sort field
    match = next((ep for ep in episodes if ep.get("sort") == target_ep_num), None)
    if match:
        return match

    # Array index match (0-based → 1-based)
    if 0 < target_ep_num <= len(episodes):
        return episodes[target_ep_num - 1]

    return None
