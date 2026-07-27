"""Centralized configuration.

Resolution order (highest to lowest):
1. Runtime override (set via API PUT /config) — in-memory only
2. Persisted file (``data/settings.json``) — survives restarts
3. Hard-coded defaults

Environment variables are **only** used as a one-shot seed when
``settings.json`` does not exist (first boot).  After that they are
ignored — use the Settings UI or edit the JSON file directly.

Sensitive keys (password, api key) are masked in get_all().
"""

import os
from typing import Any

_SENSITIVE_KEYS = {"TMDB_API_KEY", "QBITTORRENT_PASSWORD", "TVDB_API_KEY", "DEEPSEEK_API_KEY"}

_DEFAULTS: dict[str, Any] = {
    "TMDB_API_KEY": "c5b546796de52125f23b47e0dff47add",
    "TVDB_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
    "BANGUMI_UA": "JellyfinTmdbHelper/1.0 (https://github.com)",
    "API_DELAY_MS": 600,
    "PROXY_HOST": "192.168.18.55",
    "PROXY_PORT": 7890,
    "TORRENT_WATCH_DIR": "/data/torrent",
    "MIKAN_BASE_URL": "https://mikanani.me",
    "QBITTORRENT_URL": "http://192.168.18.68:8080",
    "QBITTORRENT_USERNAME": "admin",
    "QBITTORRENT_PASSWORD": "Wu_570048008",
    "QBITTORRENT_SAVE_PATH": "/Media/BD",
    "RSS_DOWNLOAD_PATH": "/Media/番剧",
    "RSS_PATH_TEMPLATE": "/{series_name}/Season {tvdb_season}/{series_name} S{tvdb_season:02d}E{tvdb_episode:02d}",
    "TORRENT_DOWNLOAD_PATH": "/data/downloads",
    "TORRENT_EXCLUDE_PATTERNS": "cds,scans,pv,cm,menu,iv,preview,mka,nced,ncop",
    "TORRENT_HARDLINK_PATH": "/Media/BD",
    "MOVIE_HARDLINK_PATH": "/Media/剧场版",
}

# Runtime overrides (set via API) — highest priority, in-memory only
_overrides: dict[str, Any] = {}

# Whether the one-shot env→file seed has been attempted
_env_seeded = False


def _ensure_env_seeded() -> None:
    """One-shot: seed settings.json from env vars if the file is missing.

    Called lazily on the first config access — avoids circular imports
    that would occur if we imported from data at module level.
    """
    global _env_seeded
    if _env_seeded:
        return
    _env_seeded = True
    try:
        from .data import init_app_settings_from_env
        init_app_settings_from_env()
    except Exception:
        pass


def _resolve(key: str) -> Any:
    """Return the effective value: override > file > default."""
    _ensure_env_seeded()
    if key in _overrides:
        return _overrides[key]
    # Check persisted file
    try:
        from .data import get_app_settings
        file_settings = get_app_settings()
        if key in file_settings:
            val = file_settings[key]
            default = _DEFAULTS[key]
            if isinstance(default, int) and not isinstance(val, int):
                return int(val)
            return val
    except Exception:
        pass
    return _DEFAULTS[key]


def get_all(*, mask_sensitive: bool = True) -> dict[str, Any]:
    """Return a copy of all current config values."""
    result = {}
    for key in _DEFAULTS:
        val = _resolve(key)
        if mask_sensitive and key in _SENSITIVE_KEYS:
            val = "***" if val else ""
        result[key] = val
    return result


def update(changes: dict[str, Any]) -> dict[str, Any]:
    """Apply runtime config changes.  Persists to settings.json.

    Returns the new effective config.
    """
    from .data import update_app_settings

    cleaned: dict[str, Any] = {}
    for key, value in changes.items():
        if key not in _DEFAULTS:
            continue
        # Never overwrite a sensitive key with an empty string (the
        # frontend sends "" for masked password fields when the user
        # didn't type anything — we treat that as "keep existing").
        if key in _SENSITIVE_KEYS and isinstance(value, str) and value == "":
            continue
        _overrides[key] = value
        cleaned[key] = value

    if cleaned:
        update_app_settings(cleaned)
    return get_all()


def reset(key: str | None = None) -> None:
    """Reset overrides back to persisted-file / default values."""
    from .data import update_app_settings

    if key:
        _overrides.pop(key, None)
        update_app_settings({key: None})  # None = remove from file
    else:
        _overrides.clear()
        try:
            from .data import _APP_SETTINGS_FILE, _atomic_write
            _atomic_write(_APP_SETTINGS_FILE, "{}")
        except Exception:
            pass


# Module-level attribute access — keeps 'from .config import TMDB_API_KEY' working
def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(name)
    if name in _DEFAULTS:
        return _resolve(name)
    raise AttributeError(name)
