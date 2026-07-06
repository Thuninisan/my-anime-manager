"""Fuzzy episode-name matching utilities.

Ported from the frontend ``MatchTable.tsx`` — used to match a Bangumi
episode name (Japanese) against TMDB episode names (in various languages).

Provides three-round fuzzy matching: exact → substring → Dice coefficient.
"""

from __future__ import annotations

import unicodedata


def normalize(s: str) -> str:
    """Normalize a string for fuzzy comparison.

    Applies NFKC normalization, converts full-width ASCII-range characters
    to half-width (e.g. ``！`` → ``!``, ``＂`` → ``"``), replaces full-width
    spaces with half-width spaces, then trims and lowercases.

    This is the Python equivalent of ``MatchTable.tsx:normalise()``.
    """
    s = unicodedata.normalize("NFKC", s)
    result: list[str] = []
    for ch in s:
        cp = ord(ch)
        # U+FF01–U+FF5E → U+0021–U+007E (full-width → half-width ASCII)
        if 0xFF01 <= cp <= 0xFF5E:
            result.append(chr(cp - 0xFF01 + 0x21))
        elif ch == "　":  # full-width space (U+3000)
            result.append(" ")
        else:
            result.append(ch)
    return "".join(result).strip().lower()


def char_similarity(a: str, b: str) -> float:
    """Character-level Dice coefficient in [0, 1].

    Treats each string as a bag of characters (after normalization).
    Higher = more similar.  Equivalent to ``MatchTable.tsx:charSimilarity()``.
    """
    sa = set(a)
    sb = set(b)
    if not sa and not sb:
        return 1.0
    overlap = len(sa & sb)
    return (2.0 * overlap) / (len(sa) + len(sb))


# Minimum Dice coefficient for a match (matches frontend threshold).
MIN_DICE_SIMILARITY = 0.55

# Overall minimum score to accept a match (more conservative than frontend
# because this runs unattended — false positives waste metadata silently).
MIN_MATCH_SCORE = 0.6


def fuzzy_match_episode(bgm_name: str, tmdb_name: str) -> float:
    """Compare a Bangumi episode name against a TMDB episode name.

    Three-tier matching (matches ``MatchTable.tsx:fuzzyMatchTmdb()``):

    1. **Exact match** after normalization → 1.0
    2. **Substring / contains** match → 0.8
    3. **Character-level Dice coefficient** → 0.0–0.79

    Args:
        bgm_name: Bangumi episode ``name`` field (original Japanese).
        tmdb_name: TMDB episode ``name`` field.

    Returns:
        Score in [0.0, 1.0].  Scores ≥ ``MIN_MATCH_SCORE`` (0.6) are
        considered a match.
    """
    a = normalize(bgm_name)
    b = normalize(tmdb_name)

    if not a or not b:
        return 0.0

    # Round 1: exact match
    if a == b:
        return 1.0

    # Round 2: substring / contains
    if a in b or b in a:
        return 0.8

    # Round 3: character-level Dice similarity
    return char_similarity(a, b)


def match_first_episode(
    bgm_first_ep_name: str,
    season_map: dict[int, dict],
    *,
    min_score: float = MIN_MATCH_SCORE,
) -> tuple[int, float] | None:
    """Find which TMDB season's first episode best matches a Bangumi episode name.

    Iterates over all seasons in *season_map*, extracts each season's first
    episode name, and runs :func:`fuzzy_match_episode` against
    *bgm_first_ep_name*.  Returns the best ``(season_number, score)`` if the
    score meets *min_score*, or ``None``.

    Args:
        bgm_first_ep_name: Bangumi's first main-story episode name (``name`` field).
        season_map: TMDB season→episodes map from :func:`tmdb_service.build_season_episode_map`.
        min_score: Minimum score to accept (default 0.6).

    Returns:
        ``(season_number, score)`` or ``None``.
    """
    best: tuple[int, float] | None = None

    for season_num, season_data in season_map.items():
        episodes = season_data.get("episodes", [])
        if not episodes:
            continue
        first_ep = episodes[0]
        tmdb_name = first_ep.get("name", "")
        if not tmdb_name:
            continue
        score = fuzzy_match_episode(bgm_first_ep_name, tmdb_name)
        if score >= min_score and (best is None or score > best[1]):
            best = (season_num, score)

    return best
