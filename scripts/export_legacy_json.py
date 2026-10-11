"""Export current SQLite subscriptions and torrents for an older release.

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    files = {
        "subscriptions.json": [legacy_subscription_export(record) for record in data.list_subscriptions()],
        "torrents.json": {"version": 1, "torrents": torrents.list_torrents()},
    }
    for name, records in files.items():
        path = args.directory / name
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Exported {len(records)} records to {path}")


if __name__ == "__main__":
    main()
