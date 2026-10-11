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

# Mapping data is stored exclusively in SQLite.
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

_mapping_data: dict[int, dict] | None = None
_mapping_revision = -1
_mapping_lock = threading.Lock()


def _load() -> dict[int, dict]:
    return _store.list_mappings()


def _mapping_cache() -> dict[int, dict]:
    global _mapping_data, _mapping_revision
    with _mapping_lock:
        revision = _store.mapping_revision()
        if _mapping_data is None or revision != _mapping_revision:
            _mapping_data = _load()
            _mapping_revision = revision
        return _mapping_data


def mapping_count() -> int:
    return _store.mapping_count()


def replace_mappings(records: dict, **options) -> None:
    global _mapping_data
    _store.replace_mappings(records, **options)
    with _mapping_lock:
        _mapping_data = None


def get_mikan_id(bangumi_id: int) -> int | None:
    mapping = _mapping_cache()
    entry = mapping.get(bangumi_id)
    return entry.get("mikan_id") if entry else None


def get_bangumi_name(bangumi_id: int) -> str | None:
    mapping = _mapping_cache()
    entry = mapping.get(bangumi_id)
    return entry.get("title_cn") if entry else None


def get_bangumi_name_original(bangumi_id: int) -> str | None:
    """Get original (Japanese) title from the mapping."""
    mapping = _mapping_cache()
    entry = mapping.get(bangumi_id)
    return entry.get("title_jp") if entry else None


def get_tmdb_id(bangumi_id: int) -> int | None:
    mapping = _mapping_cache()
    entry = mapping.get(bangumi_id)
    return entry.get("tmdb_id") if entry else None


def get_tmdb_season(bangumi_id: int) -> int | None:
    """Get TMDB season number from the Bangumi→Mikan mapping.

    Defaults to 1 when the source does not specify a season.
    """
    mapping = _mapping_cache()
    entry = mapping.get(bangumi_id)
    return entry.get("tmdb_season") if entry else None


def _set_mapping_fields(bangumi_id: int, fields: dict, *, manual: bool = True) -> bool:
    global _mapping_data
    saved = _store.set_mapping_fields(bangumi_id, fields, manual=manual)
    if saved:
        with _mapping_lock:
            _mapping_data = None
    return saved


def set_mikan_id(bangumi_id: int, mikan_id: int) -> bool:
    return _set_mapping_fields(bangumi_id, {"mikan_id": mikan_id})


def add_mapping(bangumi_id: int, name: str, name_original: str = "") -> None:
    global _mapping_data
    _store.add_mapping(bangumi_id, name, name_original)
    with _mapping_lock:
        _mapping_data = None


def set_tmdb_id(bangumi_id: int, tmdb_id: int, tmdb_season: int | None = None, *, manual: bool = True) -> bool:
    fields = {"tmdb_id": tmdb_id}
    if tmdb_season is not None:
        fields["tmdb_season"] = tmdb_season
    return _set_mapping_fields(bangumi_id, fields, manual=manual)


def get_tvdb_id(bangumi_id: int) -> int | None:
    """Get TVDB series ID from the Bangumi mapping entry."""
    mapping = _mapping_cache()
    entry = mapping.get(bangumi_id)
    return entry.get("tvdb_id") if entry else None


def get_tvdb_season(bangumi_id: int) -> int | None:
    """Get TVDB season number from the Bangumi mapping entry.

    Values follow Kometa conventions: 1+ for normal seasons,
    0 for specials, -1 for movies.
    """
    mapping = _mapping_cache()
    entry = mapping.get(bangumi_id)
    return entry.get("tvdb_season") if entry else None


def set_tvdb_id(bangumi_id: int, tvdb_id: int, tvdb_season: int | None = None, *, manual: bool = True) -> bool:
    fields = {"tvdb_id": tvdb_id}
    if tvdb_season is not None:
        fields["tvdb_season"] = tvdb_season
    return _set_mapping_fields(bangumi_id, fields, manual=manual)


def get_bangumi_id_by_tvdb_id(tvdb_id: int) -> int | None:
    """Reverse lookup: TVDB ID → Bangumi ID.

    Args:
        tvdb_id: TVDB series ID.

    Returns:
        Bangumi ID, or None if not found.
    """
    mapping = _mapping_cache()
    for bgm_id_str, entry in mapping.items():
        if entry.get("tvdb_id") == tvdb_id:
            return int(bgm_id_str)
    return None


def get_map_entries_by_tvdb_id(tvdb_id: int) -> list[dict]:
    """Return every Bangumi entry mapped to a TVDB series."""
    mapping = _mapping_cache()
    return sorted(({"bangumi_id": int(bgm_id), **entry}
                   for bgm_id, entry in mapping.items()
                   if entry.get("tvdb_id") == tvdb_id), key=lambda entry: entry["bangumi_id"])


def get_movie_map_entries_by_titles(names: list[str]) -> list[dict]:
    """Find movie entries whose Chinese or original title equals an RSS alias."""
    mapping = _mapping_cache()

    def normalized(value: str) -> str:
        return "".join(unicodedata.normalize("NFKC", value).lower().split())

    wanted = {normalized(name) for name in names if name}
    return sorted(({"bangumi_id": int(bgm_id), **entry}
                   for bgm_id, entry in mapping.items()
                   if (entry.get("tvdb_season") == 0 or
                       any(marker in str(entry.get("title_cn", "")).lower() for marker in
                           ("movie", "剧场版", "劇場版", "映画")))
                   and any(normalized(str(entry.get(key) or "")) in wanted
                           for key in ("title_cn", "title_jp"))),
                  key=lambda entry: entry["bangumi_id"])


def get_bangumi_id_by_tmdb_id(tmdb_id: int) -> int | None:
    """Reverse lookup: TMDB ID → Bangumi ID.

    Args:
        tmdb_id: TMDB series ID.

    Returns:
        Bangumi ID, or None if not found.
    """
    mapping = _mapping_cache()
    for bgm_id_str, entry in mapping.items():
        if entry.get("tmdb_id") == tmdb_id:
            return int(bgm_id_str)
    return None


def get_map_entry(bangumi_id: int) -> dict | None:
    """Get the full Bangumi→Mikan map entry for a Bangumi ID.

    Returns the raw entry dict (title_cn, title_jp, mikan_id, tmdb_id,
    tmdb_season, tvdb_id, tvdb_season, override), or None.
    """
    mapping = _mapping_cache()
    return mapping.get(bangumi_id)


def get_map_entries_by_tmdb_id(tmdb_id: int) -> list[dict]:
    """Return all map entries that share the same TMDB ID.

    Useful for ktnbytes / 343-Labs torrents where a single TMDB show
    may map to multiple Bangumi seasons (e.g. S1 + S2).

    Args:
        tmdb_id: TMDB series ID.

    Returns:
        List of dicts with keys: bangumi_id, title_cn, title_jp,
        tvdb_id, tvdb_season, tmdb_season.
        Sorted by bangumi_id ascending.
    """
    mapping = _mapping_cache()
    results: list[dict] = []
    for bgm_id_str, entry in mapping.items():
        if entry.get("tmdb_id") == tmdb_id:
            results.append({
                "bangumi_id": int(bgm_id_str),
                "title_cn": entry.get("title_cn", ""),
                "title_jp": entry.get("title_jp"),
                "tvdb_id": entry.get("tvdb_id"),
                "tvdb_season": entry.get("tvdb_season"),
                "tmdb_season": entry.get("tmdb_season"),
            })
    results.sort(key=lambda x: x["bangumi_id"])
    return results


def search_by_name(query: str) -> list[dict]:
    """Search community mappings by name. Returns up to 20 short matches."""
    mapping = _mapping_cache()
    q = query.strip().lower()
    if not q:
        return []
    results = []
    for bid_str, entry in mapping.items():
        name = entry.get("title_cn", "")
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

_subs_lock = threading.Lock()


def _load_subs() -> list[dict]:
    return _store.list_subscriptions()


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
    return _store.mutate_subscription(bangumi_id, operation)


def remove_subscription(bangumi_id: int) -> bool:
    return _store.mutate_subscription(bangumi_id,
                                      lambda existing: (None, existing is not None))


def update_subscription(bangumi_id: int, fields: dict, *, identity_source="explicit_user_mapping") -> bool:
    def operation(existing):
        if existing is None:
            return None, False
        changes = dict(fields)
        for provider in ("tmdb", "tvdb"):
            if isinstance(changes.get(provider), dict):
                changes[provider] = {**existing.get(provider, {}), **changes[provider]}
        existing.update(changes)
        existing["_identity_update_source"] = identity_source
        existing["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return existing, True
    return _store.mutate_subscription(bangumi_id, operation)


def set_subscription_rss_offset(bangumi_id: int, key: str, offset: int) -> bool:
    if key not in {"primary", "backup"}:
        raise ValueError("RSS key must be primary or backup")
    def operation(existing):
        if existing is None:
            return None, False
        existing.setdefault(key, {})["offset"] = offset
        existing["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return existing, True
    return _store.mutate_subscription(bangumi_id, operation)


# ═══════════════════════════════════════════════════════════════════════
# Download history (dedup by bangumi_id + episode_number)
# ═══════════════════════════════════════════════════════════════════════


from ..db import download_history as _history


def is_downloaded(bangumi_id: int, ep_num: int) -> bool:
    _, status = _history.get_episode(bangumi_id, ep_num)
    return status == "downloaded"


def get_episode_source(bangumi_id: int, ep_num: int) -> str | None:
    entry, _ = _history.get_episode(bangumi_id, ep_num)
    return entry.get("source") if entry else None


def get_episode_pub_date(bangumi_id: int, ep_num: int) -> str | None:
    entry, _ = _history.get_episode(bangumi_id, ep_num)
    return entry.get("pub_date") if entry else None


def remove_episode_record(bangumi_id: int, ep_num: int) -> bool:
    def operation(session, row):
        if row is None:
            return False
        session.delete(row)
        return True
    return _history.mutate_episode(bangumi_id, ep_num, operation)


def mark_downloaded(
    bangumi_id: int, ep_num: int, guid: str, source: str,
    pub_date: str = "", info_hash: str = "", *, episode_mapping=None, download_path=None,
) -> None:
    from ..db.models import DownloadEpisode
    from ..db.download_episode_mapping import save

    def operation(session, row):
        if row is None:
            row = DownloadEpisode(bangumi_id=bangumi_id, episode_number=ep_num)
            session.add(row)
        row.guid = guid
        row.source = source
        row.pub_date = pub_date
        row.info_hash = info_hash
        row.at = time.strftime("%Y-%m-%dT%H:%M:%S")
        if download_path is not None:
            row.download_path = download_path
        if episode_mapping is not None:
            save(session, row, episode_mapping)
        row.fail_count = 0
        row.status = "downloaded"
    _history.mutate_episode(bangumi_id, ep_num, operation)


def set_episode_mapping(
    bangumi_id: int, ep_num: int, tmdb_episode_number: int | float | None = None,
    tmdb_season_number: int | None = None,
) -> bool:
    from ..db.models import BangumiMapping

    def operation(session, row):
        if row is None:
            return False
        mapping = row
        if not row.mapping_initialized:
            # A manually marked record may not have a mapping yet.
            community = session.get(BangumiMapping, bangumi_id)
            if community is not None:
                row.tmdb_series_id = community.tmdb_id
                row.tmdb_season_number = community.tmdb_season
                row.tvdb_series_id = community.tvdb_id
                row.tvdb_season_number = community.tvdb_season
            row.mapping_initialized = True
        for field, value in (("tmdb_episode_number", tmdb_episode_number),
                             ("tmdb_season_number", tmdb_season_number)):
            if value is not None and getattr(mapping, field) != value:
                setattr(mapping, field, value)
                mapping.tmdb_episode_id = None
        return True
    saved = _history.mutate_episode(bangumi_id, ep_num, operation)
    if saved:
        logger.info("Episode mapping updated: bangumi=%d sort=%d TMDB season=%s episode=%s",
                    bangumi_id, ep_num, tmdb_season_number, tmdb_episode_number)
    return saved


def get_all_episodes(bangumi_id: int) -> dict[str, dict]:
    return _history.get_all(bangumi_id)


def clear_download_history(bangumi_id: int) -> int:
    return _history.clear(bangumi_id)


MAX_FAIL_COUNT = 5


def get_fail_count(bangumi_id: int, ep_num: int) -> int:
    entry, _ = _history.get_episode(bangumi_id, ep_num)
    return entry.get("fail_count", 0) if entry else 0


def increment_fail_count(bangumi_id: int, ep_num: int) -> int:
    from ..db.models import DownloadEpisode

    def operation(session, row):
        if row is None:
            row = DownloadEpisode(bangumi_id=bangumi_id, episode_number=ep_num,
                                  status="failed", fail_count=0)
            session.add(row)
        row.fail_count += 1
        return row.fail_count
    return _history.mutate_episode(bangumi_id, ep_num, operation)


def reset_fail_count(bangumi_id: int, ep_num: int) -> None:
    def operation(session, row):
        if row:
            row.fail_count = 0
    _history.mutate_episode(bangumi_id, ep_num, operation)


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
            if isinstance(default, bool):
                seeded[key] = env_val.strip().lower() in {"true", "1", "yes", "on"}
            else:
                seeded[key] = int(env_val) if isinstance(default, int) else env_val

    if seeded:
        _atomic_write(
            _APP_SETTINGS_FILE,
            json.dumps(seeded, ensure_ascii=False, indent=2),
        )
        logger.info("从环境变量初始化 settings.json: %s", list(seeded.keys()))

    return seeded if seeded else None
