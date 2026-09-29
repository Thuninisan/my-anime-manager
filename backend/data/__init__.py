"""Public data access API backed by SQLite, plus remaining JSON settings/history."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

from ..utils.paths import PACKAGE_DATA_DIR, USER_DATA_DIR
from ..db import legacy_data as _store

logger = logging.getLogger(__name__)

# Bundled mapping JSON is a first-run import source; SQLite is authoritative.
_DATA_DIR = PACKAGE_DATA_DIR

# User data (subscriptions, download_history, rss_settings) can be
# pointed at a separate directory via the MAM_DATA_DIR env var.  This
# lets Docker users mount a volume for persistence without clobbering
# the Python package's __init__.py.
_USER_DATA_DIR = USER_DATA_DIR

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
    return _store.list_mappings(_MAP_FILE)


def _mapping_cache() -> dict[int, dict]:
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    return _bangumi_mikan_map


def mapping_count() -> int:
    return _store.mapping_count(_MAP_FILE)


def replace_mappings(records: dict) -> None:
    global _bangumi_mikan_map
    _store.replace_mappings(_MAP_FILE, records)
    _bangumi_mikan_map = None


def get_mikan_id(bangumi_id: int) -> int | None:
    global _bangumi_mikan_map
    _bangumi_mikan_map = _mapping_cache()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("mikan_id") if entry else None


def get_bangumi_name(bangumi_id: int) -> str | None:
    global _bangumi_mikan_map
    _bangumi_mikan_map = _mapping_cache()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry["name"] if entry else None


def get_bangumi_name_original(bangumi_id: int) -> str | None:
    """Get original (Japanese) title from the mapping."""
    global _bangumi_mikan_map
    _bangumi_mikan_map = _mapping_cache()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("name_original") if entry else None


def get_tmdb_id(bangumi_id: int) -> int | None:
    global _bangumi_mikan_map
    _bangumi_mikan_map = _mapping_cache()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("tmdb_id") if entry else None


def get_tmdb_season(bangumi_id: int) -> int | None:
    """Get TMDB season number from the Bangumi→Mikan mapping.

    Only set when the upstream bangumi-data source includes a /season/N suffix.
    """
    global _bangumi_mikan_map
    _bangumi_mikan_map = _mapping_cache()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("tmdb_season") if entry else None


def _set_mapping_fields(bangumi_id: int, fields: dict) -> bool:
    global _bangumi_mikan_map
    saved = _store.set_mapping_fields(_MAP_FILE, bangumi_id, fields)
    if saved:
        _bangumi_mikan_map = None
    return saved


def set_mikan_id(bangumi_id: int, mikan_id: int) -> bool:
    return _set_mapping_fields(bangumi_id, {"mikan_id": mikan_id})


def set_tmdb_id(bangumi_id: int, tmdb_id: int, tmdb_season: int | None = None) -> bool:
    fields = {"tmdb_id": tmdb_id}
    if tmdb_season is not None:
        fields["tmdb_season"] = tmdb_season
    return _set_mapping_fields(bangumi_id, fields)


def get_anidb_id(bangumi_id: int) -> int | None:
    """Get AniDB ID from the Bangumi mapping entry."""
    global _bangumi_mikan_map
    _bangumi_mikan_map = _mapping_cache()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("anidb_id") if entry else None


def get_tvdb_id(bangumi_id: int) -> int | None:
    """Get TVDB series ID from the Bangumi mapping entry."""
    global _bangumi_mikan_map
    _bangumi_mikan_map = _mapping_cache()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("tvdb_id") if entry else None


def get_tvdb_season(bangumi_id: int) -> int | None:
    """Get TVDB season number from the Bangumi mapping entry.

    Values follow Kometa conventions: 1+ for normal seasons,
    0 for specials, -1 for movies.
    """
    global _bangumi_mikan_map
    _bangumi_mikan_map = _mapping_cache()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry.get("tvdb_season") if entry else None


def set_tvdb_id(bangumi_id: int, tvdb_id: int, tvdb_season: int | None = None) -> bool:
    fields = {"tvdb_id": tvdb_id}
    if tvdb_season is not None:
        fields["tvdb_season"] = tvdb_season
    return _set_mapping_fields(bangumi_id, fields)


def get_bangumi_id_by_tvdb_id(tvdb_id: int) -> int | None:
    """Reverse lookup: TVDB ID → Bangumi ID.

    Args:
        tvdb_id: TVDB series ID.

    Returns:
        Bangumi ID, or None if not found.
    """
    global _bangumi_mikan_map
    _bangumi_mikan_map = _mapping_cache()
    for bgm_id_str, entry in _bangumi_mikan_map.items():
        if entry.get("tvdb_id") == tvdb_id:
            return int(bgm_id_str)
    return None


def get_map_entries_by_tvdb_id(tvdb_id: int) -> list[dict]:
    """Return every Bangumi entry mapped to a TVDB series."""
    global _bangumi_mikan_map
    _bangumi_mikan_map = _mapping_cache()
    return sorted(({"bangumi_id": int(bgm_id), **entry}
                   for bgm_id, entry in _bangumi_mikan_map.items()
                   if entry.get("tvdb_id") == tvdb_id), key=lambda entry: entry["bangumi_id"])


def get_movie_map_entries_by_titles(names: list[str]) -> list[dict]:
    """Find movie entries whose Chinese or original title equals an RSS alias."""
    global _bangumi_mikan_map
    _bangumi_mikan_map = _mapping_cache()

    def normalized(value: str) -> str:
        return "".join(unicodedata.normalize("NFKC", value).lower().split())

    wanted = {normalized(name) for name in names if name}
    return sorted(({"bangumi_id": int(bgm_id), **entry}
                   for bgm_id, entry in _bangumi_mikan_map.items()
                   if (entry.get("tvdb_season") == 0 or
                       any(marker in str(entry.get("name", "")).lower() for marker in
                           ("movie", "剧场版", "劇場版", "映画")))
                   and any(normalized(str(entry.get(key) or "")) in wanted
                           for key in ("name", "name_original"))),
                  key=lambda entry: entry["bangumi_id"])


def get_bangumi_id_by_tmdb_id(tmdb_id: int) -> int | None:
    """Reverse lookup: TMDB ID → Bangumi ID.

    Args:
        tmdb_id: TMDB series ID.

    Returns:
        Bangumi ID, or None if not found.
    """
    global _bangumi_mikan_map
    _bangumi_mikan_map = _mapping_cache()
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
    _bangumi_mikan_map = _mapping_cache()
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
    _bangumi_mikan_map = _mapping_cache()
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
    _bangumi_mikan_map = _mapping_cache()
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
    return _store.list_subscriptions(_SUBS_FILE, _migrate_subscriptions)


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


def list_subscriptions() -> list[dict]:
    return _load_subs()


def add_subscription(name: str, rss_url: str, bangumi_id: int, subgroup_id: int,
                     subgroup_name: str, filter_tags: list[str] | None = None,
                     backup_rss_url: str = "", backup_subgroup_id: int = 0,
                     backup_subgroup_name: str = "", backup_filter_tags: list[str] | None = None,
                     download_path: str = "", exclude_patterns: list[str] | None = None,
                     backup_exclude_patterns: list[str] | None = None) -> dict:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    def operation(existing):
        primary = {"rss_url": rss_url, "subgroup_id": subgroup_id,
                   "subgroup_name": subgroup_name, "filter_tags": filter_tags or [],
                   "exclude_patterns": exclude_patterns or []}
        backup = {"rss_url": backup_rss_url, "subgroup_id": backup_subgroup_id,
                  "subgroup_name": backup_subgroup_name, "filter_tags": backup_filter_tags or [],
                  "exclude_patterns": backup_exclude_patterns or []}
        if existing:
            for key, feed, url in (("primary", primary, rss_url), ("backup", backup, backup_rss_url)):
                previous = existing.get(key, {})
                if previous.get("rss_url") == url and "offset" in previous:
                    feed["offset"] = previous["offset"]
            existing.update(name=name, primary=primary, backup=backup, updated_at=now)
            if download_path:
                existing["download_path"] = download_path
            return existing, existing
        record = {"name": name, "bangumi_id": bangumi_id,
                  "download_path": download_path or "/{series_name}/Season {season}",
                  "active": 1, "created_at": now, "primary": primary, "backup": backup}
        return record, record
    return _store.mutate_subscription(_SUBS_FILE, _migrate_subscriptions, bangumi_id, operation)


def remove_subscription(bangumi_id: int) -> bool:
    return _store.mutate_subscription(_SUBS_FILE, _migrate_subscriptions, bangumi_id,
                                      lambda existing: (None, existing is not None))


def update_subscription(bangumi_id: int, fields: dict) -> bool:
    def operation(existing):
        if existing is None:
            return None, False
        existing.update(fields)
        existing["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return existing, True
    return _store.mutate_subscription(_SUBS_FILE, _migrate_subscriptions, bangumi_id, operation)


def set_subscription_rss_offset(bangumi_id: int, key: str, offset: int) -> bool:
    if key not in {"primary", "backup"}:
        raise ValueError("RSS key must be primary or backup")
    def operation(existing):
        if existing is None:
            return None, False
        existing.setdefault(key, {})["offset"] = offset
        existing["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return existing, True
    return _store.mutate_subscription(_SUBS_FILE, _migrate_subscriptions, bangumi_id, operation)


# ═══════════════════════════════════════════════════════════════════════
# Download history (dedup by bangumi_id + episode_number)
# ═══════════════════════════════════════════════════════════════════════

_HIST_FILE = _USER_DATA_DIR / "download_history.json"


def _load_hist() -> dict:
    return _store.get_history(_HIST_FILE)


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
    def operation(hist):
        episodes = hist.setdefault("episodes", {})
        bgm_key, ep_key = str(bangumi_id), str(ep_num)
        if bgm_key not in episodes or ep_key not in episodes[bgm_key]:
            return False
        del episodes[bgm_key][ep_key]
        if not episodes[bgm_key]:
            del episodes[bgm_key]
        return True
    return _store.mutate_history(_HIST_FILE, operation)


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
    def operation(hist):
        episodes: dict[str, dict] = hist.setdefault("episodes", {})
        bgm_key, ep_key = str(bangumi_id), str(ep_num)
        existing = episodes.setdefault(bgm_key, {}).get(ep_key, {})
        episodes[bgm_key][ep_key] = {
            "rss_url": rss_url, "guid": guid, "source": source,
            "pub_date": pub_date, "info_hash": info_hash,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tmdb_ep": existing.get("tmdb_ep"),
            "tmdb_season": existing.get("tmdb_season"),
            "tvdb_ep": tvdb_ep or existing.get("tvdb_ep"),
            "tmdb_ep_calc": tmdb_ep_calc or existing.get("tmdb_ep_calc"),
        }
    _store.mutate_history(_HIST_FILE, operation)


def set_episode_overrides(
    bangumi_id: int, ep_num: int,
    tmdb_ep: int | None = None,
    tmdb_season: int | None = None,
) -> bool:
    """Set TMDB episode/season overrides for a downloaded episode.

    Returns False if the episode record doesn't exist.
    """
    def operation(hist):
        episodes: dict[str, dict] = hist.setdefault("episodes", {})
        bgm_key, ep_key = str(bangumi_id), str(ep_num)
        if bgm_key not in episodes or ep_key not in episodes[bgm_key]:
            return False
        if tmdb_ep is not None:
            episodes[bgm_key][ep_key]["tmdb_ep"] = tmdb_ep
        if tmdb_season is not None:
            episodes[bgm_key][ep_key]["tmdb_season"] = tmdb_season
        return True
    saved = _store.mutate_history(_HIST_FILE, operation)
    if not saved:
        return False
    logger.info("overrides set for bangumi=%d sort=%d: tmdb_ep=%s tmdb_season=%s",
                bangumi_id, ep_num, tmdb_ep, tmdb_season)
    return True


def get_all_episodes(bangumi_id: int) -> dict[str, dict]:
    """Return {ep_num: {rss_url, guid, source, at}, ...} for a bangumi entry."""
    hist = _load_hist()
    return hist.get("episodes", {}).get(str(bangumi_id), {})


def clear_download_history(bangumi_id: int) -> int:
    """Remove ALL download history entries for a bangumi_id. Returns count."""
    def operation(hist):
        episodes = hist.setdefault("episodes", {})
        return len(episodes.pop(str(bangumi_id), {}))
    return _store.mutate_history(_HIST_FILE, operation)


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
    def operation(hist):
        episodes: dict[str, dict] = hist.setdefault("episodes", {})
        entry = episodes.setdefault(str(bangumi_id), {}).setdefault(str(ep_num), {
            "rss_url": "", "guid": "", "source": "",
            "pub_date": "", "info_hash": "", "at": "",
        })
        entry["fail_count"] = entry.get("fail_count", 0) + 1
        return entry["fail_count"]
    return _store.mutate_history(_HIST_FILE, operation)


def reset_fail_count(bangumi_id: int, ep_num: int) -> None:
    """Clear the failure count for an episode (called after a successful download)."""
    def operation(hist):
        entry = hist.get("episodes", {}).get(str(bangumi_id), {}).get(str(ep_num))
        if entry:
            entry.pop("fail_count", None)
    _store.mutate_history(_HIST_FILE, operation)


# ═══════════════════════════════════════════════════════════════════════
# Global RSS settings (exclude patterns, etc.)
# ═══════════════════════════════════════════════════════════════════════

_SETTINGS_FILE = _USER_DATA_DIR / "rss_settings.json"
_settings_lock = threading.Lock()

_DEFAULT_SETTINGS = {
    "exclude_patterns": ["全集"],
}


def get_rss_settings() -> dict:
    saved = _load_app_settings()
    if "RSS_EXCLUDE_PATTERNS" in saved:
        return {"exclude_patterns": saved["RSS_EXCLUDE_PATTERNS"]}
    if _SETTINGS_FILE.exists():
        try:
            return json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULT_SETTINGS)


def update_rss_settings(changes: dict) -> dict:
    with _settings_lock:
        current = get_rss_settings()
        current.update(changes)
        update_app_settings({"RSS_EXCLUDE_PATTERNS": current["exclude_patterns"]})
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
    with _app_settings_lock:
        current = _load_app_settings()
        for k, v in changes.items():
            if v is None:
                current.pop(k, None)
            else:
                current[k] = v
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
