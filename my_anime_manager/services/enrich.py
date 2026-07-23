"""Subscription enrichment — Bangumi chain, TVDB/TMDB auto-inference, episode offsets."""

import logging
from typing import Any, Callable

from .. import config
from ..clients import bangumi as bgm_client
from ..clients.bangumi import get_subject
from ..data import (
    get_tmdb_id, get_tmdb_season, get_tvdb_id, get_tvdb_season,
    set_tmdb_id as data_set_tmdb_id, set_tvdb_id,
)
from ..utils.episode_name_match import fuzzy_match_episode

logger = logging.getLogger(__name__)




logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Episode offset — map RSS episode numbers to Bangumi sort range
# ═══════════════════════════════════════════════════════════════════════

_bgm_ep_cache: dict[int, list[dict]] = {}  # subject_id → episodes


async def _get_bangumi_episodes(subject_id: int) -> list[dict]:
    """Get episodes for a Bangumi subject (cached)."""
    eps = _bgm_ep_cache.get(subject_id)
    if not eps:
        try:
            eps = await bgm_get_episodes(subject_id)
            _bgm_ep_cache[subject_id] = eps
        except Exception:
            return []
    return eps


def _match_rss_ep_to_sort(episodes: list[dict], rss_ep: int) -> int:
    """Match RSS episode number to Bangumi sort value.

    Tries exact match on 'ep' field first, then positional fallback
    (rss_ep-th episode in the sorted list). Returns the sort value,
    or rss_ep as-is if no match found.
    """
    # Exact match on ep field
    for e in episodes:
        if e.get("ep") == rss_ep:
            return e.get("sort") or rss_ep
    # Positional fallback (1-based → 0-based index)
    if 0 < rss_ep <= len(episodes):
        e = episodes[rss_ep - 1]
        return e.get("sort") or rss_ep
    return rss_ep


# ═══════════════════════════════════════════════════════════════════════
# Tier-1 TMDB auto-inference helpers (used by enrich_subscription)
# ═══════════════════════════════════════════════════════════════════════


async def _build_chain_ids(root_id: int) -> list[int]:
    """Walk forward from *root_id* through sequel relations.

    Returns the ordered list of Bangumi subject IDs in the chain
    (including *root_id*).  Stops at 30 entries to prevent infinite loops.
    """
    chain_ids = [root_id]
    visited = {root_id}
    current_id = root_id
    for _ in range(30):
        try:
            relations = await _get_bangumi_relations(current_id)
        except Exception:
            break
        sequel = next(
            (r for r in relations if r.get("relation") == "续集"), None
        )
        if not sequel or sequel["id"] in visited:
            break
        visited.add(sequel["id"])
        chain_ids.append(sequel["id"])
        current_id = sequel["id"]
    return chain_ids


async def _auto_infer_tmdb(
    bangumi_id: int,
    chain_ids: list[int],
    root_subject: dict | None,
    _emit: Callable[[str], None],
) -> dict | None:
    """Try to infer a missing TMDB ID via name-based matching.

    Called when ``get_tmdb_id(bangumi_id)`` returns ``None`` / 0.

    *chain_ids* is the full Bangumi sequel chain from root (pre-computed
    by the caller to avoid duplicate API calls).

    Returns:
        ``{"tmdb_id": int, "tmdb_season": int}`` or ``None``.
    """
    from ..clients import tmdb as tmdb_client
    from ..services import tmdb as tmdb_service
    from ..utils.episode_name_match import fuzzy_match_episode

    # ── Step 1: Determine strategy ──
    is_single = len(chain_ids) == 1

    if is_single:
        # 1a: Single-entry chain — search TMDB directly
        subject = root_subject
        if subject is None:
            try:
                subject = await get_subject(bangumi_id)
            except Exception:
                pass
        if subject is None:
            return None

        original_name = (subject.get("name") or "").strip()
        if not original_name:
            return None

        _emit(f"   🔍 单条目链，直接搜索 TMDB: {original_name}")
        try:
            res = await tmdb_client.search_tv(original_name)
            results = res.json().get("results", [])
        except Exception:
            _emit("   ⚠️ TMDB 搜索请求失败")
            return None

        if not results:
            _emit("   ❌ TMDB 搜索无结果")
            return None

        best = results[0]
        _emit(
            f"   ✅ 匹配到: {best['name']} "
            f"({best.get('original_name', '')}) [id={best['id']}]"
        )
        return {"tmdb_id": best["id"], "tmdb_season": 1}

    # 1b: Multi-entry chain — collect candidates from siblings
    candidates: list[int] = []
    for cid in chain_ids:
        if cid == bangumi_id:
            continue
        ctid = get_tmdb_id(cid)
        if ctid:
            candidates.append(ctid)

    # Deduplicate while preserving order
    seen: set[int] = set()
    unique_candidates: list[int] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique_candidates.append(c)

    if not unique_candidates:
        # Fall back to searching TMDB with chain[0]'s name
        _emit("   🔍 链中无已知 TMDB ID，用 chain[0] 名称搜索...")
        root_name = ""
        try:
            if root_subject:
                root_name = (root_subject.get("name") or "").strip()
            if not root_name:
                root_subj = await get_subject(chain_ids[0])
                root_name = (root_subj.get("name") or "").strip()
        except Exception:
            pass

        if not root_name:
            _emit("   ⚠️ 无法获取 chain[0] 名称")
            return None

        try:
            res = await tmdb_client.search_tv(root_name)
            results = res.json().get("results", [])
        except Exception:
            _emit("   ⚠️ TMDB 搜索请求失败")
            return None

        if not results:
            _emit("   ❌ TMDB 搜索无结果")
            return None

        unique_candidates = [results[0]["id"]]
        _emit(
            f"   ✅ TMDB 搜索命中: {results[0]['name']} "
            f"(id={results[0]['id']})"
        )

    if not unique_candidates:
        return None

    # ── Step 2: Get Bangumi first main-story episode name ──
    _emit(f"   📡 候选 TMDB ID: {unique_candidates}")

    try:
        bgm_eps = await _get_bangumi_episodes(bangumi_id)
    except Exception:
        _emit("   ⚠️ 获取 Bangumi 剧集列表失败")
        return None

    if not bgm_eps:
        _emit("   ⚠️ Bangumi 无剧集数据")
        return None

    first_bgm_ep_name = (bgm_eps[0].get("name") or "").strip()
    if not first_bgm_ep_name:
        _emit("   ⚠️ 首个剧集名为空")
        return None

    _emit(f"   📺 Bangumi 首个剧集: {first_bgm_ep_name}")

    # ── Step 3: Fetch TMDB seasons & match ──
    best_result: tuple[int, int, float, int, str] | None = None  # (tmdb_id, season, score, ep_number, ep_name)

    for ctid in unique_candidates:
        # Determine request language from TMDB original_language
        try:
            detail_res = await tmdb_client.get_tv_detail(ctid)
            detail = detail_res.json()
            orig_lang = (detail.get("original_language") or "ja").strip().lower()
        except Exception:
            orig_lang = "ja"

        # Map to TMDB API language parameter
        lang = "ja" if orig_lang == "ja" else (
            "zh-CN" if orig_lang == "zh" else "ja"
        )
        _emit(
            f"   🌐 TMDB {ctid} original_language={orig_lang}"
            f" → 请求语言={lang}"
        )

        try:
            season_map = await tmdb_service.build_season_episode_map(
                ctid, language=lang,
            )
        except Exception:
            _emit(f"   ⚠️ TMDB {ctid} 获取季数据失败")
            continue

        for season_num, season_data in season_map.items():
            for ep in season_data.get("episodes", []):
                tmdb_name = (ep.get("name") or "").strip()
                if not tmdb_name:
                    continue
                score = fuzzy_match_episode(first_bgm_ep_name, tmdb_name)
                if score >= 0.6:
                    _emit(
                        f"   📺 tmdb={ctid} S{season_num:02d}E{ep.get('epNum', '?')} "
                        f"\"{tmdb_name}\" ↔ \"{first_bgm_ep_name}\" score={score:.3f}"
                    )
                    if best_result is None or score > best_result[2]:
                        ep_num = ep.get("epNum", 0)
                        best_result = (ctid, season_num, score, ep_num, tmdb_name)

    if best_result is None:
        _emit("   ❌ 所有候选均未达到匹配阈值")
        return None

    _emit(
        f"   ✅ 最佳匹配: tmdb_id={best_result[0]} S{best_result[1]:02d}E{best_result[3]} "
        f"\"{best_result[4]}\" ↔ \"{first_bgm_ep_name}\" score={best_result[2]:.3f}"
    )
    return {"tmdb_id": best_result[0], "tmdb_season": best_result[1], "tmdb_ep_number": best_result[3]}


async def _auto_infer_tvdb(
    bangumi_id: int,
    chain_ids: list[int],
    root_subject: dict | None,
    _emit: Callable[[str], None],
) -> dict | None:
    """Try to infer a missing TVDB ID via name-based matching.

    Called when ``get_tvdb_id(bangumi_id)`` returns ``None`` / 0.

    *chain_ids* is the full Bangumi sequel chain from root (pre-computed
    by the caller to avoid duplicate API calls).

    Returns:
        ``{"tvdb_id": int, "tvdb_season": int}`` or ``None``.
    """
    from ..clients import tvdb as tvdb_client
    from ..utils.episode_name_match import fuzzy_match_episode

    # ── Step 1: Collect sibling TVDB IDs ──
    is_single = len(chain_ids) == 1

    if is_single:
        # Single-entry chain — search TVDB directly
        subject = root_subject
        if subject is None:
            try:
                subject = await get_subject(bangumi_id)
            except Exception:
                pass
        if subject is None:
            return None

        original_name = (subject.get("name") or "").strip()
        if not original_name:
            return None

        _emit(f"   🔍 单条目链，直接搜索 TVDB: {original_name}")
        try:
            res = await tvdb_client.search_series(original_name)
            results = res.json().get("data", [])
        except Exception:
            _emit("   ⚠️ TVDB 搜索请求失败")
            return None

        if not results:
            _emit("   ❌ TVDB 搜索无结果")
            return None

        best = results[0]
        candidate_id = int(best.get("tvdb_id") or best.get("id", 0))
        if not candidate_id:
            _emit("   ⚠️ TVDB 搜索结果缺少 ID")
            return None
        _emit(f"   ✅ 匹配到: {best.get('name', '?')} [id={candidate_id}]")
        unique_candidates = [candidate_id]
    else:
        # Multi-entry chain — collect TVDB IDs from siblings
        candidates: list[int] = []
        for cid in chain_ids:
            if cid == bangumi_id:
                continue
            ctid = get_tvdb_id(cid)
            if ctid:
                candidates.append(ctid)

        seen: set[int] = set()
        unique_candidates: list[int] = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique_candidates.append(c)

        if unique_candidates:
            _emit(f"   🔗 从链中兄弟条目收集到 {len(unique_candidates)} 个 TVDB ID: {unique_candidates}")
        else:
            # Fall back to searching TVDB with chain[0]'s name
            _emit("   🔍 链中无已知 TVDB ID，用 chain[0] 名称搜索...")
            root_name = ""
            try:
                if root_subject:
                    root_name = (root_subject.get("name") or "").strip()
                if not root_name:
                    root_subj = await get_subject(chain_ids[0])
                    root_name = (root_subj.get("name") or "").strip()
            except Exception:
                pass

            if not root_name:
                _emit("   ⚠️ 无法获取 chain[0] 名称")
                return None

            try:
                res = await tvdb_client.search_series(root_name)
                results = res.json().get("data", [])
            except Exception:
                _emit("   ⚠️ TVDB 搜索请求失败")
                return None

            if not results:
                _emit("   ❌ TVDB 搜索无结果")
                return None

            candidate_id = int(results[0].get("tvdb_id") or results[0].get("id", 0))
            if not candidate_id:
                _emit("   ⚠️ TVDB 搜索结果缺少 ID")
                return None
            _emit(f"   ✅ TVDB 搜索命中: {results[0].get('name', '?')} (id={candidate_id})")
            unique_candidates = [candidate_id]

    if not unique_candidates:
        return None

    # ── Step 2: Get Bangumi first main-story episode name ──
    _emit(f"   📡 候选 TVDB ID: {unique_candidates}")

    try:
        bgm_eps = await _get_bangumi_episodes(bangumi_id)
    except Exception:
        _emit("   ⚠️ 获取 Bangumi 剧集列表失败")
        return None

    if not bgm_eps:
        _emit("   ⚠️ Bangumi 无剧集数据")
        return None

    first_bgm_ep_name = (bgm_eps[0].get("name") or "").strip()
    if not first_bgm_ep_name:
        _emit("   ⚠️ 首个剧集名为空")
        return None

    _emit(f"   📺 Bangumi 首个剧集: {first_bgm_ep_name}")

    # ── Step 3: Fetch TVDB episodes flat list & match ──
    best_result: tuple[int, int, float, str, int] | None = None  # (tvdb_id, season, score, ep_name, ep_number)

    for ctid in unique_candidates:
        try:
            eps_resp = await tvdb_client.get_series_episodes(ctid)
            eps_data = eps_resp.json().get("data", eps_resp.json())
            episodes = eps_data.get("episodes", [])
        except Exception:
            _emit(f"   ⚠️ TVDB {ctid} 获取剧集列表失败")
            continue

        for ep in episodes:
            season_num = ep.get("seasonNumber")
            if season_num is None:
                continue
            tvdb_name = (ep.get("name") or "").strip()
            if not tvdb_name:
                continue
            score = fuzzy_match_episode(first_bgm_ep_name, tvdb_name)
            if score >= 0.6:
                _emit(
                    f"   📺 tvdb={ctid} S{season_num:02d}E{ep.get('number', '?')} "
                    f"\"{tvdb_name}\" ↔ \"{first_bgm_ep_name}\" score={score:.3f}"
                )
                if best_result is None or score > best_result[2]:
                    ep_num = ep.get("number", 0)
                    best_result = (ctid, season_num, score, tvdb_name, ep_num)

    if best_result is None:
        _emit("   ❌ 所有候选均未达到匹配阈值")
        return None

    _emit(
        f"   ✅ 最佳匹配: tvdb_id={best_result[0]} S{best_result[1]:02d}E{best_result[4]} "
        f"\"{best_result[3]}\" ↔ \"{first_bgm_ep_name}\" score={best_result[2]:.3f}"
    )
    return {"tvdb_id": best_result[0], "tvdb_season": best_result[1], "tvdb_ep_number": best_result[4]}


async def _compute_tvdb_ep_offset(
    bangumi_id: int, tvdb_id: int, tvdb_season: int,
    _emit: Callable[[str], None],
) -> int:
    """Compute TVDB episode offset via episode-name matching.

    Returns ``tvdb_ep_number - bgm_ep_val``, or 0 on failure.
    """
    from ..clients import tvdb as tvdb_client
    from ..utils.episode_name_match import fuzzy_match_episode

    try:
        eps = await _get_bangumi_episodes(bangumi_id)
    except Exception:
        return 0
    if not eps:
        return 0

    first_bgm_name = (eps[0].get("name") or "").strip()
    bgm_ep_val = eps[0].get("sort") or eps[0].get("ep", 0)
    if not first_bgm_name or not bgm_ep_val:
        return 0

    try:
        eps_resp = await tvdb_client.get_series_episodes(tvdb_id)
        eps_data = eps_resp.json().get("data", eps_resp.json())
        all_eps = eps_data.get("episodes", [])
    except Exception:
        _emit("   ⚠️ 获取 TVDB 集数列表失败")
        return 0

    best_score = 0.0
    best_num = 0
    for ep in all_eps:
        if ep.get("seasonNumber") != tvdb_season:
            continue
        tvdb_name = (ep.get("name") or "").strip()
        if not tvdb_name:
            continue
        score = fuzzy_match_episode(first_bgm_name, tvdb_name)
        if score > best_score:
            best_score = score
            best_num = ep.get("number", 0)

    if best_score >= 0.6 and best_num:
        offset = best_num - bgm_ep_val
        _emit(
            f"   📐 tvdb_ep_offset={offset} "
            f"(bgm_sort={bgm_ep_val} → tvdb_ep={best_num}, score={best_score:.3f})"
        )
        return offset

    _emit(f"   ⚠️ TVDB 集名匹配失败 (bgm=\"{first_bgm_name}\", score={best_score:.3f})")
    return 0


async def _compute_tmdb_ep_offset(
    bangumi_id: int, tmdb_id: int, tmdb_season: int,
    _emit: Callable[[str], None],
) -> int:
    """Compute TMDB episode offset via episode-name matching.

    Returns ``tmdb_ep_number - bgm_ep_val``, or 0 on failure.
    """
    from ..clients import tmdb as tmdb_client
    from ..services import tmdb as tmdb_service
    from ..utils.episode_name_match import fuzzy_match_episode

    try:
        eps = await _get_bangumi_episodes(bangumi_id)
    except Exception:
        return 0
    if not eps:
        return 0

    first_bgm_name = (eps[0].get("name") or "").strip()
    bgm_ep_val = eps[0].get("sort") or eps[0].get("ep", 0)
    if not first_bgm_name or not bgm_ep_val:
        return 0

    try:
        detail_res = await tmdb_client.get_tv_detail(tmdb_id)
        detail = detail_res.json()
        orig_lang = (detail.get("original_language") or "ja").strip().lower()
    except Exception:
        orig_lang = "ja"
    lang = "ja" if orig_lang == "ja" else ("zh-CN" if orig_lang == "zh" else "ja")

    try:
        season_map = await tmdb_service.build_season_episode_map(
            tmdb_id, language=lang,
        )
    except Exception:
        _emit("   ⚠️ 获取 TMDB 季数据失败")
        return 0

    best_score = 0.0
    best_num = 0
    season_data = season_map.get(tmdb_season)
    if season_data:
        for ep in season_data.get("episodes", []):
            tmdb_name = (ep.get("name") or "").strip()
            if not tmdb_name:
                continue
            score = fuzzy_match_episode(first_bgm_name, tmdb_name)
            if score > best_score:
                best_score = score
                best_num = ep.get("epNum", 0)

    if best_score >= 0.6 and best_num:
        offset = best_num - bgm_ep_val
        _emit(
            f"   📐 tmdb_ep_offset={offset} "
            f"(bgm_sort={bgm_ep_val} → tmdb_ep={best_num}, score={best_score:.3f})"
        )
        return offset

    _emit(f"   ⚠️ TMDB 集名匹配失败 (bgm=\"{first_bgm_name}\", score={best_score:.3f})")
    return 0


async def enrich_subscription(
    bangumi_id: int,
    on_progress: Callable[[str], Any] | None = None,
) -> dict | None:
    """Enrich a subscription with Bangumi season info, sort range, rating.

    Backtracks prequel relations to determine bgm_season (position in the
    series) without a full forward chain traversal — saves ~14 API calls
    per enrichment.

    Called once when a subscription is added (or lazily when an
    existing subscription is first downloaded).  Returns fields
    to write into subscriptions.json.

    Args:
        bangumi_id: Bangumi subject ID.
        on_progress: Optional callback — when provided, progress messages
            are sent to this callback instead of printed to stdout.
            Used by the NDJSON streaming endpoint.

    Returns:
        dict with bgm_season, bgm_sortrange, tmdb_id, tmdb_season,
        bgm_rating, air_date, bgm_subject_name — or None on failure.
    """
    def _emit(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        print(msg)

    try:
        _emit(f"🔗 丰富化订阅信息 (bgm_id={bangumi_id})...")

        # 1. Backtrack prequels to find root + count depth → bgm_season
        # No need for full forward chain traversal — depth from root
        # gives us the season number directly, saving ~14 API calls per
        # enrichment compared to build_bangumi_chain().
        visited: set[int] = set()
        current_id = bangumi_id
        depth = 0  # number of prequel steps back to root
        root_name = ""
        root_id = bangumi_id
        root_subject = None

        for _ in range(30):
            visited.add(current_id)
            try:
                relations = await _get_bangumi_relations(current_id)
            except Exception:
                _emit("⚠️ 获取 Bangumi 关系失败")
                return None

            prequel = next(
                (r for r in relations if r.get("relation") == "前传"), None
            )
            if not prequel or prequel["id"] in visited:
                # Reached root — fetch its name for series_name
                root_id = current_id
                try:
                    root_subject = await get_subject(root_id)
                    root_name = (
                        root_subject.get("name_cn") or root_subject.get("name") or ""
                    ).strip()
                except Exception:
                    root_name = ""
                _emit(
                    f"   🔗 回溯前传: {prequel.get('name_cn') or prequel['name']} "
                    f"(id: {prequel['id']})"
                ) if prequel else None
                break

            _emit(
                f"   🔗 回溯前传: {prequel.get('name_cn') or prequel['name']} "
                f"(id: {prequel['id']})"
            )
            depth += 1
            current_id = prequel["id"]

        bgm_season = depth + 1
        _emit(f"✅ bgm_season={bgm_season}")
        series_name = root_name
        _emit(f"✅ series_name={series_name}")

        # Build forward chain once — reused by both TVDB and TMDB auto-inference
        chain_ids = await _build_chain_ids(root_id)

        # 2. Get sort range
        eps = await _get_bangumi_episodes(bangumi_id)
        sorts = [e.get("sort") or e.get("ep", 0) for e in eps]
        bgm_sortrange = [min(sorts), max(sorts)] if sorts else [0, 0]
        _emit(f"✅ bgm_sortrange={bgm_sortrange}")

        # 3. Get rating + air_date from subject API (non-fatal)
        bgm_rating = 0.0
        bgm_rating_total = 0
        air_date = ""
        try:
            # reuse root_subject if it's the same as bangumi_id, else fetch
            if bangumi_id == root_id:
                subject_data = root_subject
            else:
                subject_data = await get_subject(bangumi_id)
            rating = subject_data.get("rating")
            if rating and isinstance(rating, dict):
                bgm_rating = float(rating.get("score") or 0)
                bgm_rating_total = int(rating.get("total") or 0)
            air_date = (subject_data.get("date") or "").strip()
            _emit(f"✅ bgm_rating={bgm_rating} (total={bgm_rating_total})")
            if air_date:
                _emit(f"✅ air_date={air_date}")
        except Exception:
            _emit("⚠️ Failed to fetch Bangumi rating/air_date (non-fatal)")

        # 4. TMDB info from bangumi_mikan_map.json
        tmdb_id = get_tmdb_id(bangumi_id)
        tmdb_season = get_tmdb_season(bangumi_id)

        # 4a. TVDB info from bangumi_mikan_map.json
        tvdb_id = get_tvdb_id(bangumi_id)
        tvdb_season = get_tvdb_season(bangumi_id)

        # ── Tier-1 fallback: auto-infer missing TVDB ID ──
        tvdb_auto_ep_number: int | None = None
        if not tvdb_id:
            _emit("🔍 TVDB ID 缺失，启动自动推断...")
            try:
                tvdb_result = await _auto_infer_tvdb(
                    bangumi_id, chain_ids, root_subject, _emit,
                )
                if tvdb_result:
                    tvdb_id = tvdb_result["tvdb_id"]
                    tvdb_season = tvdb_result["tvdb_season"]
                    tvdb_auto_ep_number = tvdb_result.get("tvdb_ep_number")
                    from ..data import set_tvdb_id as _persist_tvdb
                    _persist_tvdb(bangumi_id, tvdb_id, tvdb_season)
                    _emit(
                        f"✅ TVDB 自动推断成功: "
                        f"tvdb_id={tvdb_id}, tvdb_season={tvdb_season}"
                    )
                else:
                    _emit("⚠️ 自动匹配 TVDB 失败，请在订阅卡片中手动设置")
            except Exception as e:
                _emit(f"⚠️ TVDB 自动推断异常: {e}")

        # ── Tier-1 fallback: auto-infer missing TMDB ID ──
        tmdb_auto_ep_number: int | None = None
        if not tmdb_id:
            _emit("🔍 TMDB ID 缺失，启动自动推断...")
            try:
                fallback_result = await _auto_infer_tmdb(
                    bangumi_id, chain_ids, root_subject, _emit,
                )
                if fallback_result:
                    tmdb_id = fallback_result["tmdb_id"]
                    tmdb_season = fallback_result["tmdb_season"]
                    tmdb_auto_ep_number = fallback_result.get("tmdb_ep_number")
                    # Persist so future lookups are instant
                    data_set_tmdb_id(bangumi_id, tmdb_id, tmdb_season)
                    _emit(
                        f"✅ TMDB 自动推断成功: "
                        f"tmdb_id={tmdb_id}, tmdb_season={tmdb_season}"
                    )
                else:
                    _emit("⚠️ 自动匹配 TMDB 失败，请在订阅卡片中手动设置")
            except Exception as e:
                _emit(f"⚠️ TMDB 自动推断异常: {e}")

        # 4b. Compute episode offsets via name matching ──
        #     Auto-infer already has the matched ep number; for known IDs
        #     we run the matching fresh to ensure offset is always accurate.
        tmdb_ep_offset = 0
        if tmdb_id and tmdb_season:
            if tmdb_auto_ep_number is not None:
                eps = await _get_bangumi_episodes(bangumi_id)
                bgm_ep_v = eps[0].get("sort") or eps[0].get("ep", 0) if eps else 0
                if bgm_ep_v:
                    tmdb_ep_offset = tmdb_auto_ep_number - bgm_ep_v
                    _emit(f"   📐 tmdb_ep_offset={tmdb_ep_offset} (from auto-infer: bgm_sort={bgm_ep_v} → tmdb_ep={tmdb_auto_ep_number})")
            else:
                tmdb_ep_offset = await _compute_tmdb_ep_offset(
                    bangumi_id, tmdb_id, tmdb_season, _emit,
                )

        tvdb_ep_offset = 0
        if tvdb_id and tvdb_season:
            if tvdb_auto_ep_number is not None:
                eps = await _get_bangumi_episodes(bangumi_id)
                bgm_ep_v = eps[0].get("sort") or eps[0].get("ep", 0) if eps else 0
                if bgm_ep_v:
                    tvdb_ep_offset = tvdb_auto_ep_number - bgm_ep_v
                    _emit(f"   📐 tvdb_ep_offset={tvdb_ep_offset} (from auto-infer: bgm_sort={bgm_ep_v} → tvdb_ep={tvdb_auto_ep_number})")
            else:
                tvdb_ep_offset = await _compute_tvdb_ep_offset(
                    bangumi_id, tvdb_id, tvdb_season, _emit,
                )

        # 5. Extract this season's Bangumi subject name for file naming
        bgm_subject_name = ""
        try:
            if bangumi_id == root_id:
                sd = root_subject
            else:
                sd = await get_subject(bangumi_id)
            bgm_subject_name = (
                sd.get("name_cn") or sd.get("name") or ""
            ).strip()
        except Exception:
            pass

        return {
            "bgm_season": bgm_season,
            "bgm_sortrange": bgm_sortrange,
            "series_name": series_name,
            "bgm_subject_name": bgm_subject_name,
            "tmdb_id": tmdb_id or 0,
            "tmdb_season": tmdb_season,
            "tvdb_id": tvdb_id or 0,
            "tvdb_season": tvdb_season,
            "tmdb_ep_offset": tmdb_ep_offset,
            "tvdb_ep_offset": tvdb_ep_offset,
            "bgm_rating": bgm_rating,
            "bgm_rating_total": bgm_rating_total,
            "air_date": air_date,
        }
    except Exception as e:
        _emit(f"⚠️ enrich_subscription 失败: {e}")
        traceback.print_exc()
        return None




_bgm_ep_id_cache: dict[int, dict[int, int]] = {}  # {bangumi_id: {sort: episode_id}}
_bgm_relations_cache: dict[int, list[dict]] = {}   # {subject_id: relations}


async def _get_bangumi_relations(subject_id: int) -> list[dict]:
    """Get relations for a Bangumi subject (cached)."""
    rels = _bgm_relations_cache.get(subject_id)
    if rels is None:
        rels = await bgm_client.get_relations(subject_id)
        _bgm_relations_cache[subject_id] = rels
    return rels


async def _get_bangumi_ep_id(bangumi_id: int, sort: int) -> int | None:
    """Look up the Bangumi episode ID for a given *sort* number.

    Fetches the full episode list on first call for a *bangumi_id* and
    caches the mapping in memory for subsequent calls.
    """
    if bangumi_id in _bgm_ep_id_cache:
        return _bgm_ep_id_cache[bangumi_id].get(sort)

    try:
        eps = await _get_bangumi_episodes(bangumi_id)
    except Exception:
        logger.warning("Failed to fetch Bangumi episodes for id=%d", bangumi_id)
        return None

    sort_to_id: dict[int, int] = {}
    for ep in eps:
        ep_sort = ep.get("sort") or ep.get("ep", 0)
        if ep_sort:
            sort_to_id[ep_sort] = ep["id"]
    _bgm_ep_id_cache[bangumi_id] = sort_to_id

    return sort_to_id.get(sort)



