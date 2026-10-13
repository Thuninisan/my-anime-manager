"""Shared media naming and path-template vocabulary."""

def sanitize_path_name(name: str) -> str:
    """Replace ``/`` and ``\\`` with a space, collapsing whitespace.

    Since ``/`` is always a path separator on Linux/macOS and ``\\`` is
    always one on Windows, neither can appear in a file or directory
    name.  Replacing with a space keeps the name readable (e.g.
    "Fate stay night") without creating spurious directory levels.
    """
    sanitized = name.replace("/", " ").replace("\\", " ")
    return " ".join(sanitized.split())


def format_download_path(
    template: str, sub: dict, sort: int = 0, ext: str = "",
    bangumi_sort: int | None = None, bangumi_ep: int | None = None,
    tvdb_episode: int | None = None, tmdb_episode: int | None = None,
    tmdb_title: str = "",
) -> str:
    """Format a download path template with subscription and episode variables.

    Uses Python ``str.format()`` syntax.  Available variables:

    ==================== ============================================
    ``{series_name}``    Root series name (from Bangumi chain)
    ``{bangumi_title}``  Current season Bangumi entry name
    ``{bgm_season}``     Bangumi chain position (int)
    ``{tvdb_season}``    TVDB season number (int)
    ``{tmdb_season}``    TMDB season number (int)
    ``{bangumi_sort}``   Bangumi sort number (sequential within entry)
    ``{bangumi_ep}``     Bangumi canonical episode number
    ``{tvdb_episode}``   Matched TVDB episode number
    ``{tmdb_episode}``   Matched TMDB episode number
    ``{tmdb_title}``     TMDB show title (Chinese)
    ``{sort}``           Alias for ``{bangumi_sort}`` (deprecated)
    ==================== ============================================

    Format specs are supported, e.g. ``{tvdb_episode:02d}`` → ``05``.
    The *ext* parameter (e.g. ``".mkv"``) is appended after formatting.
    """
    bgm = sub.get("bgm", {})
    tvdb = sub.get("tvdb", {})
    tmdb = sub.get("tmdb", {})
    # Use ``is not None`` guards — season 0 is a valid value (Specials)
    # and must not be treated as falsy by ``or``.
    _tvdb_s = tvdb.get("season")
    _tmdb_s = tmdb.get("season")
    _bgm_s = bgm.get("season", 1)
    effective_sort = bangumi_sort if bangumi_sort is not None else sort
    effective_bangumi_episode = bangumi_ep if bangumi_ep is not None else effective_sort
    return template.format(
        series_name=sanitize_path_name(sub.get("series_name") or sub.get("name", "")),
        bangumi_title=sanitize_path_name(bgm.get("subject_name") or sub.get("name", "")),
        bgm_season=_bgm_s,
        tvdb_season=_tvdb_s if _tvdb_s is not None else _bgm_s,
        tmdb_season=_tmdb_s if _tmdb_s is not None else _bgm_s,
        sort=effective_sort,
        bangumi_sort=effective_sort,
        bangumi_ep=effective_bangumi_episode,
        tvdb_episode=tvdb_episode if tvdb_episode is not None else effective_bangumi_episode,
        tmdb_episode=tmdb_episode if tmdb_episode is not None else effective_sort,
        tmdb_title=sanitize_path_name(tmdb_title or sub.get("series_name") or sub.get("name", "")),
    ) + ext
