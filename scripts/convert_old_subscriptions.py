"""
Convert docs/subscriptions.json (old flat format) to the current nested format.

Old format — flat keys:
  rss_url, subgroup_id, subgroup_name, filter_tags, exclude_patterns
  backup_rss_url, backup_subgroup_id, backup_subgroup_name,
  backup_filter_tags, backup_exclude_patterns
  bgm_season, bgm_sortrange, series_name, bgm_subject_name,
  bgm_rating, bgm_rating_total, air_date
  tmdb_id, tmdb_season, tmdb_ep_offset

New format (matching enrich_subscription output):
  primary:  { rss_url, subgroup_id, subgroup_name, filter_tags, exclude_patterns }
  backup:   { rss_url, subgroup_id, subgroup_name, filter_tags, exclude_patterns }
  bgm:      { season, sortrange, series_name, subject_name, rating, air_date }
  tvdb:     { id, season, ep_offset }
  tmdb:     { id, season, ep_offset }

Usage:
  python scripts/convert_old_subscriptions.py
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

OLD_PATH = os.path.join(PROJECT_DIR, "docs", "subscriptions.json")
OUT_PATH = os.path.join(PROJECT_DIR, "docs", "subscriptions_new.json")


def convert_entry(old: dict) -> dict:
    """Convert a single old-format entry to the new format."""
    new: dict = {}

    # ── Top-level fields (unchanged) ──
    for key in (
        "name", "bangumi_id", "download_path", "active",
        "created_at", "poster_url", "updated_at",
    ):
        new[key] = old.get(key)

    # ── primary RSS ──
    new["primary"] = {
        "rss_url": old.get("rss_url", ""),
        "subgroup_id": old.get("subgroup_id", 0),
        "subgroup_name": old.get("subgroup_name", ""),
        "filter_tags": old.get("filter_tags", []),
        "exclude_patterns": old.get("exclude_patterns", []),
    }

    # ── backup RSS ──
    new["backup"] = {
        "rss_url": old.get("backup_rss_url", ""),
        "subgroup_id": old.get("backup_subgroup_id", 0),
        "subgroup_name": old.get("backup_subgroup_name", ""),
        "filter_tags": old.get("backup_filter_tags", []),
        "exclude_patterns": old.get("backup_exclude_patterns", []),
    }

    # ── bgm (Bangumi enrichment fields) ──
    new["bgm"] = {
        "season": old.get("bgm_season", 1),
        "sortrange": old.get("bgm_sortrange", [0, 0]),
        "series_name": old.get("series_name", ""),
        "subject_name": old.get("bgm_subject_name", ""),
        "rating": old.get("bgm_rating", 0.0),
        "air_date": old.get("air_date", ""),
    }
    # Note: bgm_rating_total is intentionally dropped (not in current format)

    # ── tvdb (absent in old format — fill with defaults) ──
    new["tvdb"] = {
        "id": 0,
        "season": None,
        "ep_offset": 0,
    }

    # ── tmdb ──
    new["tmdb"] = {
        "id": old.get("tmdb_id") or 0,
        "season": old.get("tmdb_season"),
        "ep_offset": old.get("tmdb_ep_offset", 0),
    }

    return new


def main():
    if not os.path.exists(OLD_PATH):
        print(f"Old file not found: {OLD_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(OLD_PATH, "r", encoding="utf-8") as f:
        old_data = json.load(f)

    new_data = [convert_entry(e) for e in old_data]

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)

    print(f"Converted {len(new_data)} entries → {OUT_PATH}")


if __name__ == "__main__":
    main()
