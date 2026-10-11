"""Export current SQLite subscriptions, history and torrents for an older release.

Usage: python scripts/export_legacy_json.py /path/to/export-directory
Stop the app before downgrading, then copy the generated files to their
respective legacy locations. Community mappings are no longer exported.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import data
from backend.db import torrents


def legacy_subscription_export(record):
    """Explicit downgrade projection, never used by runtime repositories."""
    import copy
    result = copy.deepcopy(record)
    identity = result.get("resource_identity")
    if identity is not None:
        tid = identity["tmdb_movie_id"] if identity["media_type"] == "movie" else identity["tmdb_series_id"]
        result["tmdb"] = {**result.get("tmdb", {}), "id": tid or 0}
        result["tvdb"] = {**result.get("tvdb", {}), "id": identity["tvdb_series_id"] or 0}
    return result


def legacy_history_export(document):
    """Project relational RSS mappings to the old history format."""
    import copy
    result = copy.deepcopy(document)
    for episodes in result.get("episodes", {}).values():
        for entry in episodes.values():
            mapping = entry.pop("mapping", None)
            entry.pop("download_path", None)
            if mapping:
                entry["tmdb_ep_calc"] = mapping["tmdb_episode_number"]
                entry["tmdb_ep"] = mapping["tmdb_episode_number"]
                entry["tmdb_season"] = mapping["tmdb_season_number"]
                entry["tvdb_ep"] = mapping["tvdb_episode_number"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    files = {
        "subscriptions.json": [legacy_subscription_export(record) for record in data.list_subscriptions()],
        "download_history.json": legacy_history_export(data._load_hist()),
        "torrents.json": {"version": 1, "torrents": torrents.list_torrents()},
    }
    for name, records in files.items():
        path = args.directory / name
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Exported {len(records)} records to {path}")


if __name__ == "__main__":
    main()
