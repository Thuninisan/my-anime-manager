"""NFO XML file generator for Jellyfin metadata."""

import re
from pathlib import Path


def _escape_xml(s: str | None) -> str:
    """Escape XML special characters.

    Args:
        s: Input string

    Returns:
        XML-safe string
    """
    if not s:
        return ""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _sanitize_filename(name: str | None) -> str:
    """Remove illegal characters from a filename.

    Args:
        name: Input filename

    Returns:
        Sanitized filename
    """
    if not name:
        return "unknown"
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


def generate_episode_nfo(
    show_name: str,
    episode_name: str,
    plot: str,
    air_date: str,
    runtime: int,
    season_number: int,
    episode_number: int,
    bangumi_ep_id: int | None,
    original_name: str,
    bangumi_subject_name: str,
    directors: list[str] | None = None,
    writers: list[str] | None = None,
    actors: list[dict] | None = None,
    thumb_path: str = "",
    studios: list[str] | None = None,
    rating: float = 0.0,
    output_dir: str = ".",
    tvdb_ep_id: int = 0,
    file_stem: str = "",
) -> str:
    """Generate an episode NFO file.

    Args:
        show_name: Show title
        episode_name: Episode title
        plot: Episode overview / plot
        air_date: Air date (YYYY-MM-DD)
        runtime: Runtime in minutes
        season_number: Season number
        episode_number: Episode number
        bangumi_ep_id: Bangumi episode ID
        original_name: Original show name (Japanese)
        bangumi_subject_name: Bangumi subject name (for NFO filename fallback)
        directors: List of director names
        writers: List of writer names
        actors: List of {name, character} dicts
        thumb_path: Local thumbnail filename
        studios: List of studio/network names
        rating: Episode rating
        output_dir: Output directory
        tvdb_ep_id: TVDB episode ID
        file_stem: Base filename (without extension), from path template.
            If empty, falls back to ``{bangumi_subject_name} {ep:02d}``.

    Returns:
        Path to the generated NFO file
    """
    if directors is None:
        directors = []
    if writers is None:
        writers = []
    if actors is None:
        actors = []
    if studios is None:
        studios = []

    # Filename: use template-derived stem if provided, else legacy pattern
    if file_stem:
        file_base = _sanitize_filename(file_stem)
    else:
        file_base = _sanitize_filename(
            bangumi_subject_name or original_name or show_name
        )
        file_base = f"{file_base} {episode_number:02d}"
    filename = f"{file_base}.nfo"
    file_path = Path(output_dir) / filename

    # Skip if already exists
    if file_path.exists():
        print(f"   ⏭️ 已存在，跳过: {file_path}")
        return str(file_path)

    # Ensure directory exists
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Year from air date
    year = air_date.split("-")[0] if air_date else ""

    # Build XML fragments
    rating_tag = f"  <rating>{rating:.1f}</rating>" if rating > 0 else ""
    thumb_tag = f"  <thumb>{_escape_xml(thumb_path)}</thumb>" if thumb_path else ""
    tvdbid_tag = f"  <tvdbid>{tvdb_ep_id}</tvdbid>" if tvdb_ep_id else ""

    xml = f"""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<episodedetails>
  <title>{_escape_xml(episode_name)}</title>
  <originaltitle>{_escape_xml(original_name)}</originaltitle>
  <showtitle>{_escape_xml(show_name)}</showtitle>
  <season>{season_number}</season>
  <episode>{episode_number}</episode>
  <year>{year}</year>
  <bangumiid>{bangumi_ep_id or ''}</bangumiid>
  <plot>{_escape_xml(plot)}</plot>
  <aired>{air_date or ''}</aired>
  <premiered>{air_date or ''}</premiered>
  <runtime>{runtime or ''}</runtime>
{rating_tag + chr(10) if rating_tag else ''}{thumb_tag + chr(10) if thumb_tag else ''}{tvdbid_tag + chr(10) if tvdbid_tag else ''}</episodedetails>
"""
    file_path.write_text(xml, encoding="utf-8")
    return str(file_path)


def generate_tv_show_nfo(
    title: str,
    original_title: str,
    plot: str,
    output_dir: str = ".",
    tvdb_id: int = 0,
    tmdb_id: int = 0,
) -> str:
    """Generate a tvshow.nfo file.

    Args:
        title: Show title (zh-CN)
        original_title: Original show name
        plot: Show overview (zh-CN)
        output_dir: Output directory
        tvdb_id: TVDB series ID
        tmdb_id: TMDB show ID

    Returns:
        Path to the generated NFO file
    """
    tvdb_tag = f"  <tvdbid>{tvdb_id}</tvdbid>" if tvdb_id else ""
    tmdb_tag = f"  <tmdbid>{tmdb_id}</tmdbid>" if tmdb_id else ""

    xml = f"""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<tvshow>
  <title>{_escape_xml(title)}</title>
  <originaltitle>{_escape_xml(original_title)}</originaltitle>
  <plot>{_escape_xml(plot)}</plot>
{tvdb_tag + chr(10) if tvdb_tag else ''}{tmdb_tag + chr(10) if tmdb_tag else ''}</tvshow>
"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    file_path = output_path / "tvshow.nfo"

    # Skip if already exists
    if file_path.exists():
        print(f"   ⏭️ 已存在，跳过: {file_path}")
        return str(file_path)

    file_path.write_text(xml, encoding="utf-8")
    return str(file_path)


def generate_season_nfo(
    title: str,
    original_title: str,
    plot: str,
    premiered: str,
    season_number: int,
    bangumi_id: int,
    output_dir: str = ".",
    tvdb_season_id: int = 0,
) -> str:
    """Generate a season.nfo file (data from Bangumi subject).

    Args:
        title: Bangumi Chinese name (name_cn)
        original_title: Bangumi original name
        plot: Bangumi subject summary
        premiered: Air date (YYYY-MM-DD)
        season_number: Season number
        bangumi_id: Bangumi subject ID
        output_dir: Output directory
        tvdb_season_id: TVDB season ID (0 if unavailable).

    Returns:
        Path to the generated NFO file
    """
    year = premiered.split("-")[0] if premiered else ""

    bangumi_tag = f"  <bangumiid>{bangumi_id or ''}</bangumiid>"
    tvdb_tag = f"  <tvdbid>{tvdb_season_id}</tvdbid>" if tvdb_season_id else ""

    xml = f"""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<season>
  <title>{_escape_xml(title)}</title>
  <originaltitle>{_escape_xml(original_title)}</originaltitle>
  <plot>{_escape_xml(plot)}</plot>
  <premiered>{premiered or ''}</premiered>
  <year>{year}</year>
  <seasonnumber>{season_number}</seasonnumber>
{bangumi_tag}
{tvdb_tag + chr(10) if tvdb_tag else ''}</season>
"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    file_path = output_path / "season.nfo"

    # Skip if already exists
    if file_path.exists():
        print(f"   ⏭️ 已存在，跳过: {file_path}")
        return str(file_path)

    file_path.write_text(xml, encoding="utf-8")
    return str(file_path)
