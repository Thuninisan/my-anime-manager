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
