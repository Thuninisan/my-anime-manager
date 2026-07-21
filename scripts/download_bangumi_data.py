"""Download bangumi-data from CDN and extract Bangumi → Mikan ID mapping.

Usage:
    python scripts/download_bangumi_data.py

Writes a compact ``{bangumi_id: mikan_id}`` JSON mapping to
``my_anime_manager/data/bangumi_mikan_map.json``.
"""

import json
import re
import urllib.request
from pathlib import Path

BANGUMI_DATA_URL = "https://unpkg.com/bangumi-data@0.3/dist/data.json"
KOMETA_ANIME_IDS_URL = "https://raw.githubusercontent.com/Kometa-Team/Anime-IDs/master/anime_ids.json"
OUTPUT_FILE = Path(__file__).parent.parent / "my_anime_manager" / "data" / "bangumi_mikan_map.json"


def main() -> None:
    print(f"[download] Fetching bangumi-data from {BANGUMI_DATA_URL} ...")
    with urllib.request.urlopen(BANGUMI_DATA_URL) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    items = data.get("items") or data
    if not isinstance(items, list):
        raise ValueError(f"Unexpected data format: expected list, got {type(items)}")

    print(f"   [ok] {len(items)} entries")

    # ── Download Kometa Anime-IDs (AniDB → TVDB cross-reference) ──────
    print(f"[download] Fetching Kometa Anime-IDs from {KOMETA_ANIME_IDS_URL} ...")
    try:
        with urllib.request.urlopen(KOMETA_ANIME_IDS_URL) as resp:
            kometa_raw = json.loads(resp.read().decode("utf-8"))
        print(f"   [ok] {len(kometa_raw)} entries")
    except Exception as e:
        print(f"   [warn] Failed to download Kometa Anime-IDs: {e}")
        print(f"   [warn] TVDB cross-reference will be skipped.")
        kometa_raw = {}

    # Build integer-keyed lookup: AniDB ID → Kometa entry
    anidb_to_kometa: dict[int, dict] = {}
    for anidb_id_str, entry in kometa_raw.items():
        try:
            anidb_id = int(anidb_id_str)
        except (ValueError, TypeError):
            continue
        anidb_to_kometa[anidb_id] = entry

    mapping: dict[str, dict] = {}
    tvdb_count = 0

    for item in items:
        sites = item.get("sites", [])
        bangumi_id = None
        mikan_id = None
        tmdb_id = None
        tmdb_season = None
        anidb_id = None
        for s in sites:
            sid = str(s.get("id", ""))
            if s.get("site") == "bangumi" and sid:
                bangumi_id = sid
            elif s.get("site") == "mikan" and sid:
                mikan_id = sid
            elif s.get("site") == "tmdb" and sid:
                m = re.match(r'(?:tv|movie)/(\d+)(?:/season/(\d+))?', sid)
                if m:
                    tmdb_id = int(m.group(1))
                    tmdb_season = int(m.group(2)) if m.group(2) else None
            elif s.get("site") == "anidb" and sid:
                anidb_id = int(sid)
        if bangumi_id:
            title_trans = item.get("titleTranslate", {})
            zh_hans = title_trans.get("zh-Hans", [])
            name = zh_hans[0] if zh_hans else item.get("title", "")
            name_original = item.get("title", "")
            entry = {
                "name": name,
                "name_original": name_original,
                "mikan_id": int(mikan_id) if mikan_id else None,
                "anidb_id": anidb_id,
            }
            if tmdb_id:
                entry["tmdb_id"] = tmdb_id
            if tmdb_season:
                entry["tmdb_season"] = tmdb_season
            # Cross-reference AniDB → TVDB via Kometa dataset
            if anidb_id is not None and anidb_id in anidb_to_kometa:
                k_entry = anidb_to_kometa[anidb_id]
                k_tvdb_id = k_entry.get("tvdb_id")
                if k_tvdb_id is not None:
                    entry["tvdb_id"] = int(k_tvdb_id)
                    tvdb_count += 1
                k_tvdb_season = k_entry.get("tvdb_season")
                if k_tvdb_season is not None:
                    entry["tvdb_season"] = int(k_tvdb_season)
            mapping[bangumi_id] = entry

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"   [ok] Mapped: {len(mapping)} Bangumi entries")
    print(f"   [ok] With TVDB ID: {tvdb_count}")
    print(f"   [ok] Written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
