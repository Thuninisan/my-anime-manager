"""Centralized configuration from environment variables.

Supports runtime mutation via update() — values changed at runtime take
precedence over environment variables, and all module-level attribute
access (config.TMDB_API_KEY, etc.) reflects the current value.

Sensitive keys (password, api key) are masked in get_all().
"""

import os
from typing import Any

_SENSITIVE_KEYS = {"TMDB_API_KEY", "QBITTORRENT_PASSWORD"}

_DEFAULTS: dict[str, Any] = {
    "TMDB_API_KEY": "c5b546796de52125f23b47e0dff47add",
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
    "TORRENT_DOWNLOAD_PATH": "/data/downloads",
    "TORRENT_EXCLUDE_PATTERNS": "cds,scans,pv,cm,menu,iv,preview,mka,nced,ncop",
}

# Runtime overrides (set via API) — empty dict means "use env / default"
_overrides: dict[str, Any] = {}


def _resolve(key: str) -> Any:
    """Return the effective value: override > env var > default."""
    if key in _overrides:
        return _overrides[key]
    env_val = os.environ.get(key)
    if env_val is not None:
        default = _DEFAULTS[key]
        if isinstance(default, int):
            return int(env_val)
        return env_val
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
    """Apply runtime config changes. Returns the new effective config."""
    for key, value in changes.items():
        if key in _DEFAULTS:
            _overrides[key] = value
    return get_all()


def reset(key: str | None = None) -> None:
    """Reset overrides back to environment / default values."""
    if key:
        _overrides.pop(key, None)
    else:
        _overrides.clear()


# Module-level attribute access — keeps 'from .config import TMDB_API_KEY' working
def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(name)
    if name in _DEFAULTS:
        return _resolve(name)
    raise AttributeError(name)
