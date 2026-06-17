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
    tmdb_show_name: str,
    tmdb_ep_name: str,
    tmdb_ep_overview: str,
    tmdb_ep_air_date: str,
    tmdb_ep_runtime: int,
    tmdb_ep_id: int,
    season_number: int,
    episode_number: int,
    bangumi_ep_id: int | None,
    tmdb_original_name: str,
    bangumi_subject_name: str,
    directors: list[str] | None = None,
    writers: list[str] | None = None,
    actors: list[dict] | None = None,
    thumb_path: str = "",
    studios: list[str] | None = None,
    output_dir: str = ".",
) -> str:
    """Generate an episode NFO file.

    Args:
        tmdb_show_name: TMDB show name (Chinese title)
        tmdb_ep_name: TMDB episode title
        tmdb_ep_overview: TMDB episode overview
        tmdb_ep_air_date: Air date (YYYY-MM-DD)
        tmdb_ep_runtime: Runtime in minutes
        tmdb_ep_id: TMDB episode ID
        season_number: Season number (position in Bangumi chain)
        episode_number: Episode number (Bangumi sort)
        bangumi_ep_id: Bangumi episode ID
        tmdb_original_name: TMDB original show name (Japanese)
        bangumi_subject_name: Bangumi subject name (for NFO filename)
        directors: List of director names
        writers: List of writer names
        actors: List of {name, character} dicts
        thumb_path: Local thumbnail filename
        studios: List of studio/network names
        output_dir: Output directory

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

    season_str = f"{season_number:02d}"
    episode_str = f"{episode_number:02d}"

    # Filename: Bangumi subject name + sort episode number
    file_base = _sanitize_filename(
        bangumi_subject_name or tmdb_original_name or tmdb_show_name
    )
    filename = f"{file_base} {episode_str}.nfo"
    file_path = Path(output_dir) / filename

    # Skip if already exists
    if file_path.exists():
        print(f"   ⏭️ 已存在，跳过: {file_path}")
        return str(file_path)

    # Ensure directory exists
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Year from air date
    year = tmdb_ep_air_date.split("-")[0] if tmdb_ep_air_date else ""

    # Build XML fragments
    director_tags = "\n".join(
        f"  <director>{_escape_xml(d)}</director>" for d in directors
    )
    credits_tags = "\n".join(
        f"  <credits>{_escape_xml(w)}</credits>" for w in writers
    )
    actor_tags = "\n".join(
        f"  <actor>\n"
        f"    <name>{_escape_xml(a['name'])}</name>\n"
        f"    <role>{_escape_xml(a['character'])}</role>\n"
        f"  </actor>"
        for a in actors
    )
    studio_tags = "\n".join(
        f"  <studio>{_escape_xml(s)}</studio>" for s in studios
    )
    thumb_tag = f"  <thumb>{_escape_xml(thumb_path)}</thumb>" if thumb_path else ""

    xml = f"""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<episodedetails>
  <title>{_escape_xml(tmdb_ep_name)}</title>
  <originaltitle>{_escape_xml(tmdb_original_name)}</originaltitle>
  <showtitle>{_escape_xml(tmdb_show_name)}</showtitle>
  <season>{season_number}</season>
  <episode>{episode_number}</episode>
  <year>{year}</year>
  <bangumiid>{bangumi_ep_id or ''}</bangumiid>
  <plot>{_escape_xml(tmdb_ep_overview)}</plot>
  <aired>{tmdb_ep_air_date or ''}</aired>
  <premiered>{tmdb_ep_air_date or ''}</premiered>
  <runtime>{tmdb_ep_runtime or ''}</runtime>
{director_tags + chr(10) if director_tags else ''}{credits_tags + chr(10) if credits_tags else ''}{actor_tags + chr(10) if actor_tags else ''}{studio_tags + chr(10) if studio_tags else ''}{thumb_tag + chr(10) if thumb_tag else ''}  <uniqueid type="tmdb" default="true">{tmdb_ep_id}</uniqueid>
</episodedetails>
"""
    file_path.write_text(xml, encoding="utf-8")
    return str(file_path)


def generate_tv_show_nfo(
    title: str,
    original_title: str,
    plot: str,
    premiered: str,
    tmdb_id: int,
    genres: list[str] | None = None,
    studios: list[str] | None = None,
    rating: float = 0.0,
    status: str = "",
    output_dir: str = ".",
) -> str:
    """Generate a tvshow.nfo file (data from TMDB).

    Args:
        title: TMDB Chinese name
        original_title: TMDB original name (Japanese)
        plot: Show overview
        premiered: First air date (YYYY-MM-DD)
        tmdb_id: TMDB show ID
        genres: List of genre names
        studios: List of studio/network names
        rating: Vote average
        status: Show status (Ended / Returning Series)
        output_dir: Output directory

    Returns:
        Path to the generated NFO file
    """
    if genres is None:
        genres = []
    if studios is None:
        studios = []

    year = premiered.split("-")[0] if premiered else ""

    genre_tags = "\n".join(
        f"  <genre>{_escape_xml(g)}</genre>" for g in genres
    )
    studio_tags = "\n".join(
        f"  <studio>{_escape_xml(s)}</studio>" for s in studios
    )
    rating_tag = f"  <rating>{rating:.1f}</rating>" if rating > 0 else ""
    status_tag = f"  <status>{_escape_xml(status)}</status>" if status else ""

    xml = f"""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<tvshow>
  <title>{_escape_xml(title)}</title>
  <originaltitle>{_escape_xml(original_title)}</originaltitle>
  <plot>{_escape_xml(plot)}</plot>
  <premiered>{premiered or ''}</premiered>
  <year>{year}</year>
{genre_tags + chr(10) if genre_tags else ''}{studio_tags + chr(10) if studio_tags else ''}{rating_tag + chr(10) if rating_tag else ''}{status_tag + chr(10) if status_tag else ''}  <uniqueid type="tmdb" default="true">{tmdb_id}</uniqueid>
</tvshow>
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

    Returns:
        Path to the generated NFO file
    """
    year = premiered.split("-")[0] if premiered else ""

    xml = f"""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<season>
  <title>{_escape_xml(title)}</title>
  <originaltitle>{_escape_xml(original_title)}</originaltitle>
  <plot>{_escape_xml(plot)}</plot>
  <premiered>{premiered or ''}</premiered>
  <year>{year}</year>
  <seasonnumber>{season_number}</seasonnumber>
  <uniqueid type="bangumi">{bangumi_id or ''}</uniqueid>
</season>
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
