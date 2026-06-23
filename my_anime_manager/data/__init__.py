"""Data layer — Bangumi-Mikan mapping, RSS subscriptions, download history.

All persisted as JSON files under ``my_anime_manager/data/``.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_DATA_DIR = Path(__file__).parent

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
    return entry["mikan_id"] if entry else None


def get_bangumi_name(bangumi_id: int) -> str | None:
    global _bangumi_mikan_map
    if _bangumi_mikan_map is None:
        _bangumi_mikan_map = _load()
    entry = _bangumi_mikan_map.get(bangumi_id)
    return entry["name"] if entry else None


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


# ═══════════════════════════════════════════════════════════════════════
# RSS Subscriptions
# ═══════════════════════════════════════════════════════════════════════

_SUBS_FILE = _DATA_DIR / "subscriptions.json"
_subs_lock = threading.Lock()


def _load_subs() -> list[dict]:
    if _SUBS_FILE.exists():
        try:
            return json.loads(_SUBS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


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
) -> dict:
    """Add or update a subscription by bangumi_id (simple upsert)."""
    subs = _load_subs()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    for s in subs:
        if s["bangumi_id"] == bangumi_id:
            s["name"] = name
            s["rss_url"] = rss_url
            s["subgroup_id"] = subgroup_id
            s["subgroup_name"] = subgroup_name
            s["filter_tags"] = filter_tags or []
            s["backup_rss_url"] = backup_rss_url
            s["backup_subgroup_id"] = backup_subgroup_id
            s["backup_subgroup_name"] = backup_subgroup_name
            s["backup_filter_tags"] = backup_filter_tags or []
            if download_path:
                s["download_path"] = download_path
            s["updated_at"] = now
            _save_subs(subs)
            return s

    sub = {
        "name": name,
        "rss_url": rss_url,
        "bangumi_id": bangumi_id,
        "subgroup_id": subgroup_id,
        "subgroup_name": subgroup_name,
        "filter_tags": filter_tags or [],
        "backup_rss_url": backup_rss_url,
        "backup_subgroup_id": backup_subgroup_id,
        "backup_subgroup_name": backup_subgroup_name,
        "backup_filter_tags": backup_filter_tags or [],
        "download_path": download_path or f"/{name}/Season {{season}}",
        "active": 1,
        "created_at": now,
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


# ═══════════════════════════════════════════════════════════════════════
# Download history (dedup by bangumi_id + episode_number)
# ═══════════════════════════════════════════════════════════════════════

_HIST_FILE = _DATA_DIR / "download_history.json"
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
) -> None:
    """Record a downloaded episode, overwriting any prior record for the same ep."""
    hist = _load_hist()
    episodes: dict[str, dict] = hist.setdefault("episodes", {})
    bgm_key = str(bangumi_id)
    ep_key = str(ep_num)
    episodes.setdefault(bgm_key, {})[ep_key] = {
        "rss_url": rss_url,
        "guid": guid,
        "source": source,
        "pub_date": pub_date,
        "info_hash": info_hash,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _save_hist(hist)


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


# ═══════════════════════════════════════════════════════════════════════
# Global RSS settings (exclude patterns, etc.)
# ═══════════════════════════════════════════════════════════════════════

_SETTINGS_FILE = _DATA_DIR / "rss_settings.json"
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
