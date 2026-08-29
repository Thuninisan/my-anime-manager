"""Data layer — Bangumi-Mikan mapping, RSS subscriptions, download history.

All persisted as JSON files under ``my_anime_manager/data/``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Bundled data (bangumi_mikan_map.json) stays in the Python package —
# it ships with the image and should never be overlaid by a volume mount.
_DATA_DIR = Path(__file__).parent

# User data (subscriptions, download_history, rss_settings) can be
# pointed at a separate directory via the MAM_DATA_DIR env var.  This
# lets Docker users mount a volume for persistence without clobbering
# the Python package's __init__.py.
_USER_DATA_DIR = Path(os.environ["MAM_DATA_DIR"]) if os.environ.get("MAM_DATA_DIR") else _DATA_DIR

# Ensure the user-data directory exists (relevant when MAM_DATA_DIR
# points to a volume mount that may be empty on first boot).
if _USER_DATA_DIR != _DATA_DIR:
    _USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════
# Bangumi → Mikan mapping
# ═══════════════════════════════════════════════════════════════════════

_MAP_FILE = _DATA_DIR / "bangumi_mikan_map.json"
_bangumi_mikan_map: dict[int, dict] | None = None


def _load() -> dict[int, dict]:
    if not _MAP_FILE.exists():
        raise FileNotFoundError(
            f"Bangumi-Mikan mapping not found at {_MAP_FILE}. "
            "Run: python scripts/download_bangumi_data.py"
        )
    raw = json.loads(_MAP_FILE.read_text(encoding="utf-8"))
    return {int(k): v for k, v in raw.items()}


def get_mikan_id(bangumi_id: int) -> int | None:
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("mikan_id") if entry else None


def get_bangumi_name(bangumi_id: int) -> str | None:
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry["name"] if entry else None


def get_bangumi_name_original(bangumi_id: int) -> str | None:
    """Get original (Japanese) title from the mapping."""
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("name_original") if entry else None


def get_tmdb_id(bangumi_id: int) -> int | None:
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("tmdb_id") if entry else None


def get_tmdb_season(bangumi_id: int) -> int | None:
    """Get TMDB season number from the Bangumi→Mikan mapping.

    Only set when the upstream bangumi-data source includes a /season/N suffix.
    """
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("tmdb_season") if entry else None


def _save_map() -> None:
    """Persist the in-memory Bangumi-Mikan map back to JSON."""
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        return
    raw = {str(k): v for k, v in _bangumi_mikan_map.items()}
    _MAP_FILE.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def set_mikan_id(bangumi_id: int, mikan_id: int) -> bool:
    """Set or update mikan_id for a Bangumi entry. Returns False if not found."""
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    entry = _bangumi_mikan_map.get(bangumi_id)
    if entry is None:
        return False
    entry["mikan_id"] = mikan_id
    _save_map()
    return True


def set_tmdb_id(
    bangumi_id: int, tmdb_id: int, tmdb_season: int | None = None
) -> bool:
    """Set tmdb_id (and optionally tmdb_season) for a Bangumi entry.

    Updates the in-memory map and persists to JSON.  Used by the Tier-1
    auto-inference fallback and the Tier-2 manual override endpoint so
    that subsequent lookups are instant.

    Returns False if the Bangumi entry is not found in the map.
    """
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    entry = _bangumi_mikan_map.get(bangumi_id)
    if entry is None:
        return False
    entry["tmdb_id"] = tmdb_id
    if tmdb_season is not None:
        entry["tmdb_season"] = tmdb_season
    _save_map()
    return True


def get_anidb_id(bangumi_id: int) -> int | None:
    """Get AniDB ID from the Bangumi mapping entry."""
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("anidb_id") if entry else None


def get_tvdb_id(bangumi_id: int) -> int | None:
    """Get TVDB series ID from the Bangumi mapping entry."""
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("tvdb_id") if entry else None


def get_tvdb_season(bangumi_id: int) -> int | None:
    """Get TVDB season number from the Bangumi mapping entry.

    Values follow Kometa conventions: 1+ for normal seasons,
    0 for specials, -1 for movies.
    """
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("tvdb_season") if entry else None


def set_tvdb_id(
    bangumi_id: int, tvdb_id: int, tvdb_season: int | None = None
) -> bool:
    """Set TVDB ID (and optionally season) for a Bangumi entry.

    Updates the in-memory map and persists to JSON.  Enables future
    runtime enrichment (e.g., manual override or auto-inference).

    Returns False if the Bangumi entry is not found in the map.
    """
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    entry = _bangumi_mikan_map.get(bangumi_id)
    if entry is None:
        return False
    entry["tvdb_id"] = tvdb_id
    if tvdb_season is not None:
        entry["tvdb_season"] = tvdb_season
    _save_map()
    return True


def get_bangumi_id_by_tvdb_id(tvdb_id: int) -> int | None:
    """Reverse lookup: TVDB ID → Bangumi ID.

    Args:
        tvdb_id: TVDB series ID.

    Returns:
        Bangumi ID, or None if not found.
    """
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    for bgm_id_str, entry in _bangumi_mikan_map.items():
        if entry.get("tvdb_id") == tvdb_id:
            return int(bgm_id_str)
    return None


def get_bangumi_id_by_tmdb_id(tmdb_id: int) -> int | None:
    """Reverse lookup: TMDB ID → Bangumi ID.

    Args:
        tmdb_id: TMDB series ID.

    Returns:
        Bangumi ID, or None if not found.
    """
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    for bgm_id_str, entry in _bangumi_mikan_map.items():
        if entry.get("tmdb_id") == tmdb_id:
            return int(bgm_id_str)
    return None


def get_map_entry(bangumi_id: int) -> dict | None:
    """Get the full Bangumi→Mikan map entry for a Bangumi ID.

    Returns the raw entry dict (name, name_original, mikan_id, tmdb_id,
    tmdb_season, tvdb_id, tvdb_season, anidb_id, ...), or None.
    """
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    return _bangumi_mikan_map.get(bangumi_id)


def get_map_entries_by_tmdb_id(tmdb_id: int) -> list[dict]:
    """Return all map entries that share the same TMDB ID.

    Useful for ktnbytes / 343-Labs torrents where a single TMDB show
    may map to multiple Bangumi seasons (e.g. S1 + S2).

    Args:
        tmdb_id: TMDB series ID.

    Returns:
        List of dicts with keys: bangumi_id, name, name_original,
        tvdb_id, tvdb_season, tmdb_season.
        Sorted by bangumi_id ascending.
    """
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    results: list[dict] = []
    for bgm_id_str, entry in _bangumi_mikan_map.items():
        if entry.get("tmdb_id") == tmdb_id:
            results.append({
                "bangumi_id": int(bgm_id_str),
                "name": entry.get("name", ""),
                "name_original": entry.get("name_original"),
                "tvdb_id": entry.get("tvdb_id"),
                "tvdb_season": entry.get("tvdb_season"),
                "tmdb_season": entry.get("tmdb_season"),
            })
    results.sort(key=lambda x: x["bangumi_id"])
    return results


def search_by_name(query: str) -> list[dict]:
    """Search bangumi_mikan_map by name. Returns up to 20 short matches."""
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    q = query.strip().lower()
    if not q:
        return []
    results = []
    for bid_str, entry in _bangumi_mikan_map.items():
        name = entry.get("name", "")
        if q in name.lower():
            results.append({
                "bangumi_id": int(bid_str),
                "name": name,
                "has_mikan_id": entry.get("mikan_id") is not None,
            })
    results.sort(key=lambda r: len(r["name"]))  # shorter = closer match
    return results[:20]


# ═══════════════════════════════════════════════════════════════════════
# RSS Subscriptions
# ═══════════════════════════════════════════════════════════════════════

_SUBS_FILE = _USER_DATA_DIR / "subscriptions.json"
_subs_lock = threading.Lock()


def _load_subs() -> list[dict]:
    if _SUBS_FILE.exists():
        try:
            data = json.loads(_SUBS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if _migrate_subscriptions(data):
            _atomic_write(_SUBS_FILE, json.dumps(data, ensure_ascii=False, indent=2))
        return data
    return []


def _migrate_subscriptions(data: list[dict]) -> bool:
    """Migrate old flat subscription format to nested groups.

    Returns True if any migration was performed.
    """
    migrated = False
    for sub in data:
        if "bgm" in sub or "primary" in sub:
            continue  # already migrated (or created in new format)

        sub["bgm"] = {
            "season": sub.pop("bgm_season", 1),
            "sortrange": sub.pop("bgm_sortrange", [0, 0]),
            "subject_name": sub.pop("bgm_subject_name", sub.pop("name", "")),
            "series_name": sub.pop("series_name", ""),
            "rating": sub.pop("bgm_rating", 0.0),
            "air_date": sub.pop("air_date", ""),
        }
        sub.pop("bgm_rating_total", None)

        sub["tvdb"] = {
            "id": sub.pop("tvdb_id", 0) or 0,
            "season": sub.pop("tvdb_season", None),
            "ep_offset": sub.pop("tvdb_ep_offset", 0),
        }
        sub["tmdb"] = {
            "id": sub.pop("tmdb_id", 0) or 0,
            "season": sub.pop("tmdb_season", None),
            "ep_offset": sub.pop("tmdb_ep_offset", 0),
        }

        sub["primary"] = {
            "rss_url": sub.pop("rss_url", ""),
            "subgroup_id": sub.pop("subgroup_id", 0),
            "subgroup_name": sub.pop("subgroup_name", ""),
            "filter_tags": sub.pop("filter_tags", []),
            "exclude_patterns": sub.pop("exclude_patterns", []),
        }
        sub["backup"] = {
            "rss_url": sub.pop("backup_rss_url", ""),
            "subgroup_id": sub.pop("backup_subgroup_id", 0),
            "subgroup_name": sub.pop("backup_subgroup_name", ""),
            "filter_tags": sub.pop("backup_filter_tags", []),
            "exclude_patterns": sub.pop("backup_exclude_patterns", []),
        }
        migrated = True
    return migrated


def _save_subs(subs: list[dict]) -> None:
    with _subs_lock:
        _SUBS_FILE.write_text(
            json.dumps(subs, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def list_subscriptions() -> list[dict]:
    return _load_subs()


def add_subscription(
    name: str,
    rss_url: str,
    bangumi_id: int,
    subgroup_id: int,
    subgroup_name: str,
    filter_tags: list[str] | None = None,
    backup_rss_url: str = "",
    backup_subgroup_id: int = 0,
    backup_subgroup_name: str = "",
    backup_filter_tags: list[str] | None = None,
    download_path: str = "",
    exclude_patterns: list[str] | None = None,
    backup_exclude_patterns: list[str] | None = None,
) -> dict:
    """Add or update a subscription by bangumi_id (simple upsert)."""
    subs = _load_subs()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    for s in subs:
        if s["bangumi_id"] == bangumi_id:
            # Preserve existing offsets when the corresponding URL is unchanged.
            # Without this, subscribing to a second RSS feed would wipe
            # the offset computed by the first enrichment run.
            old_primary_offset = s.get("primary", {}).get("offset")
            old_backup_offset = s.get("backup", {}).get("offset")
            s["name"] = name
            s["primary"] = {
                "rss_url": rss_url,
                "subgroup_id": subgroup_id,
                "subgroup_name": subgroup_name,
                "filter_tags": filter_tags or [],
                "exclude_patterns": exclude_patterns or [],
            }
            s["backup"] = {
                "rss_url": backup_rss_url,
                "subgroup_id": backup_subgroup_id,
                "subgroup_name": backup_subgroup_name,
                "filter_tags": backup_filter_tags or [],
                "exclude_patterns": backup_exclude_patterns or [],
            }
            if old_primary_offset is not None and rss_url == s["primary"]["rss_url"]:
                s["primary"]["offset"] = old_primary_offset
            if old_backup_offset is not None and backup_rss_url == s["backup"]["rss_url"]:
                s["backup"]["offset"] = old_backup_offset
            if download_path:
                s["download_path"] = download_path
            s["updated_at"] = now
            _save_subs(subs)
            return s

    sub = {
        "name": name,
        "bangumi_id": bangumi_id,
        "download_path": download_path or f"/{{series_name}}/Season {{season}}",
        "active": 1,
        "created_at": now,
        "primary": {
            "rss_url": rss_url,
            "subgroup_id": subgroup_id,
            "subgroup_name": subgroup_name,
            "filter_tags": filter_tags or [],
            "exclude_patterns": exclude_patterns or [],
        },
        "backup": {
            "rss_url": backup_rss_url,
            "subgroup_id": backup_subgroup_id,
            "subgroup_name": backup_subgroup_name,
            "filter_tags": backup_filter_tags or [],
            "exclude_patterns": backup_exclude_patterns or [],
        },
    }
    subs.append(sub)
    _save_subs(subs)
    return sub


def remove_subscription(bangumi_id: int) -> bool:
    subs = _load_subs()
    before = len(subs)
    subs = [s for s in subs if s["bangumi_id"] != bangumi_id]
    if len(subs) == before:
        return False
    _save_subs(subs)
    return True


def update_subscription(bangumi_id: int, fields: dict) -> bool:
    """Update specific fields of a subscription by bangumi_id.

    Returns False if the subscription is not found.
    """
    subs = _load_subs()
    for s in subs:
        if s["bangumi_id"] == bangumi_id:
            s.update(fields)
            s["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            _save_subs(subs)
            return True
    return False


def set_subscription_rss_offset(bangumi_id: int, key: str, offset: int) -> bool:
    """Set the RSS *offset* on a subscription's primary or backup feed.

    *key* must be ``"primary"`` or ``"backup"``.  The offset is stored
    inside ``sub[key]["offset"]`` and controls how RSS episode numbers
    are mapped to Bangumi sort values (``sort = rss_ep + offset``).

    Returns False if the subscription is not found.
    """
    subs = _load_subs()
    for s in subs:
        if s["bangumi_id"] == bangumi_id:
            s.setdefault(key, {})["offset"] = offset
            s["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            _save_subs(subs)
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════
# Download history (dedup by bangumi_id + episode_number)
# ═══════════════════════════════════════════════════════════════════════

_HIST_FILE = _USER_DATA_DIR / "download_history.json"
_hist_lock = threading.Lock()


def _load_hist() -> dict:
    if _HIST_FILE.exists():
        try:
            return json.loads(_HIST_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_hist(hist: dict) -> None:
    with _hist_lock:
        _HIST_FILE.write_text(
            json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def is_downloaded(bangumi_id: int, ep_num: int) -> bool:
    """Check whether a specific episode of a bangumi entry is already downloaded."""
    hist = _load_hist()
    episodes = hist.get("episodes", {})
    return str(ep_num) in episodes.get(str(bangumi_id), {})


def get_episode_source(bangumi_id: int, ep_num: int) -> str | None:
    """Return 'primary', 'backup', or None for a downloaded episode."""
    hist = _load_hist()
    episodes = hist.get("episodes", {})
    entry = episodes.get(str(bangumi_id), {}).get(str(ep_num))
    return entry.get("source") if entry else None


def get_episode_pub_date(bangumi_id: int, ep_num: int) -> str | None:
    """Return the pub_date of a downloaded episode, or None."""
    hist = _load_hist()
    episodes = hist.get("episodes", {})
    entry = episodes.get(str(bangumi_id), {}).get(str(ep_num))
    return entry.get("pub_date") if entry else None


def remove_episode_record(bangumi_id: int, ep_num: int) -> bool:
    """Remove a single episode record from download history. Returns True if deleted."""
    hist = _load_hist()
    episodes = hist.setdefault("episodes", {})
    bgm_key = str(bangumi_id)
    ep_key = str(ep_num)
    if bgm_key in episodes and ep_key in episodes[bgm_key]:
        del episodes[bgm_key][ep_key]
        # Clean up empty subject entries
        if not episodes[bgm_key]:
            del episodes[bgm_key]
        _save_hist(hist)
        return True
    return False


def mark_downloaded(
    bangumi_id: int,
    ep_num: int,
    rss_url: str,
    guid: str,
    source: str,
    pub_date: str = "",
    info_hash: str = "",
    tvdb_ep: int = 0,
    tmdb_ep_calc: int = 0,
) -> None:
    """Record a downloaded episode, overwriting any prior record for the same ep.

    Preserves existing ``tmdb_ep`` and ``tmdb_season`` override fields if present.
    """
    hist = _load_hist()
    episodes: dict[str, dict] = hist.setdefault("episodes", {})
    bgm_key = str(bangumi_id)
    ep_key = str(ep_num)
    # Preserve existing overrides
    existing = episodes.setdefault(bgm_key, {}).get(ep_key, {})
    episodes[bgm_key][ep_key] = {
        "rss_url": rss_url,
        "guid": guid,
        "source": source,
        "pub_date": pub_date,
        "info_hash": info_hash,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tmdb_ep": existing.get("tmdb_ep"),
        "tmdb_season": existing.get("tmdb_season"),
        "tvdb_ep": tvdb_ep or existing.get("tvdb_ep"),
        "tmdb_ep_calc": tmdb_ep_calc or existing.get("tmdb_ep_calc"),
    }
    _save_hist(hist)


def set_episode_overrides(
    bangumi_id: int, ep_num: int,
    tmdb_ep: int | None = None,
    tmdb_season: int | None = None,
) -> bool:
    """Set TMDB episode/season overrides for a downloaded episode.

    Returns False if the episode record doesn't exist.
    """
    hist = _load_hist()
    episodes: dict[str, dict] = hist.setdefault("episodes", {})
    bgm_key = str(bangumi_id)
    ep_key = str(ep_num)
    if bgm_key not in episodes or ep_key not in episodes[bgm_key]:
        return False
    if tmdb_ep is not None:
        episodes[bgm_key][ep_key]["tmdb_ep"] = tmdb_ep
    if tmdb_season is not None:
        episodes[bgm_key][ep_key]["tmdb_season"] = tmdb_season
    _save_hist(hist)
    logger.info("overrides set for bangumi=%d sort=%d: tmdb_ep=%s tmdb_season=%s",
                bangumi_id, ep_num, tmdb_ep, tmdb_season)
    return True


def get_all_episodes(bangumi_id: int) -> dict[str, dict]:
    """Return {ep_num: {rss_url, guid, source, at}, ...} for a bangumi entry."""
    hist = _load_hist()
    return hist.get("episodes", {}).get(str(bangumi_id), {})


def clear_download_history(bangumi_id: int) -> int:
    """Remove ALL download history entries for a bangumi_id. Returns count."""
    hist = _load_hist()
    episodes = hist.setdefault("episodes", {})
    bgm_key = str(bangumi_id)
    count = len(episodes.get(bgm_key, {}))
    if bgm_key in episodes:
        del episodes[bgm_key]
        _save_hist(hist)
    return count


# ── Failure count tracking ───────────────────────────────────────────
# When a .torrent download fails repeatedly we persist a fail_count so
# the downloader can eventually give up instead of retrying forever.

MAX_FAIL_COUNT = 5


def get_fail_count(bangumi_id: int, ep_num: int) -> int:
    """Return the consecutive failure count for an episode, or 0."""
    hist = _load_hist()
    episodes = hist.get("episodes", {})
    entry = episodes.get(str(bangumi_id), {}).get(str(ep_num))
    return entry.get("fail_count", 0) if entry else 0


def increment_fail_count(bangumi_id: int, ep_num: int) -> int:
    """Increment the failure count for an episode and return the new value.

    If no history entry exists yet a minimal stub is created (without the
    fields that ``mark_downloaded`` would normally fill in — the stub only
    carries ``fail_count`` so the filter can skip the item).
    """
    hist = _load_hist()
    episodes: dict[str, dict] = hist.setdefault("episodes", {})
    bgm_key = str(bangumi_id)
    ep_key = str(ep_num)
    episodes.setdefault(bgm_key, {})
    entry = episodes[bgm_key].setdefault(ep_key, {
        "rss_url": "",
        "guid": "",
        "source": "",
        "pub_date": "",
        "info_hash": "",
        "at": "",
    })
    entry["fail_count"] = entry.get("fail_count", 0) + 1
    _save_hist(hist)
    return entry["fail_count"]


def reset_fail_count(bangumi_id: int, ep_num: int) -> None:
    """Clear the failure count for an episode (called after a successful download)."""
    hist = _load_hist()
    episodes = hist.get("episodes", {})
    entry = episodes.get(str(bangumi_id), {}).get(str(ep_num))
    if entry and "fail_count" in entry:
        del entry["fail_count"]
        _save_hist(hist)


# ═══════════════════════════════════════════════════════════════════════
# Global RSS settings (exclude patterns, etc.)
# ═══════════════════════════════════════════════════════════════════════

_SETTINGS_FILE = _USER_DATA_DIR / "rss_settings.json"
_settings_lock = threading.Lock()

_DEFAULT_SETTINGS = {
    "exclude_patterns": ["全集"],
}


def get_rss_settings() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            return json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULT_SETTINGS)


def update_rss_settings(changes: dict) -> dict:
    current = get_rss_settings()
    current.update(changes)
    with _settings_lock:
        _SETTINGS_FILE.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return current


# ═══════════════════════════════════════════════════════════════════════
# Application settings (persisted to settings.json)
# ═══════════════════════════════════════════════════════════════════════

_APP_SETTINGS_FILE = _USER_DATA_DIR / "settings.json"
_app_settings_lock = threading.Lock()

# Defaults here mirror config.py _DEFAULTS — used as the base for
# merge-on-read so that old files missing newly-added keys still work.
_APP_SETTINGS_DEFAULTS: dict[str, Any] = {}


def _load_app_settings() -> dict:
    """Read the persisted settings file.  Returns {} if missing or corrupt."""
    if _APP_SETTINGS_FILE.exists():
        try:
            return json.loads(_APP_SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("settings.json 损坏，将使用默认值")
    return {}


def get_app_settings() -> dict:
    """Return the effective application settings.

    Merges defaults on top of the persisted file so that keys added in
    newer versions of the app are never missing.
    """
    file_values = _load_app_settings()
    return {**_APP_SETTINGS_DEFAULTS, **file_values}


def _atomic_write(path: Path, data: str) -> None:
    """Write *data* to *path* atomically (tmp + rename)."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(data, encoding="utf-8")
    # On Windows, os.replace fails if the target exists — but tmp is unique
    # and target may not exist yet.  Use replace for atomicity when possible.
    try:
        os.replace(tmp, path)
    except OSError:
        # Fallback: some edge cases on Windows (different drives, etc.)
        path.write_text(data, encoding="utf-8")
        tmp.unlink(missing_ok=True)


def update_app_settings(changes: dict) -> dict:
    """Merge *changes* into the persisted settings file.

    Keys set to ``None`` are removed from the file (reset to default).
    Returns the full merged state.
    """
    current = _load_app_settings()
    for k, v in list(changes.items()):
        if v is None:
            current.pop(k, None)
        else:
            current[k] = v
    with _app_settings_lock:
        _atomic_write(
            _APP_SETTINGS_FILE,
            json.dumps(current, ensure_ascii=False, indent=2),
        )
    return {**_APP_SETTINGS_DEFAULTS, **current}


def init_app_settings_from_env() -> dict | None:
    """One-shot: if settings.json does not exist, seed it from environment
    variables that match known config keys.

    Called once at startup.  After the file exists, environment variables
    are never read again.

    Returns the written dict, or None if the file already existed.
    """
    if _APP_SETTINGS_FILE.exists():
        return None

    # Import here to avoid circular imports (data → config → data)
    from ..config import _DEFAULTS as CONFIG_DEFAULTS

    seeded: dict[str, Any] = {}
    for key in CONFIG_DEFAULTS:
        env_val = os.environ.get(key)
        if env_val is not None:
            default = CONFIG_DEFAULTS[key]
            seeded[key] = int(env_val) if isinstance(default, int) else env_val

    if seeded:
        _atomic_write(
            _APP_SETTINGS_FILE,
            json.dumps(seeded, ensure_ascii=False, indent=2),
        )
        logger.info("从环境变量初始化 settings.json: %s", list(seeded.keys()))

    return seeded if seeded else None
