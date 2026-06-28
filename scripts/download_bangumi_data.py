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
OUTPUT_FILE = Path(__file__).parent.parent / "my_anime_manager" / "data" / "bangumi_mikan_map.json"


def main() -> None:
    print(f"[download] Fetching bangumi-data from {BANGUMI_DATA_URL} ...")
    with urllib.request.urlopen(BANGUMI_DATA_URL) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    items = data.get("items") or data
    if not isinstance(items, list):
        raise ValueError(f"Unexpected data format: expected list, got {type(items)}")

    print(f"   [ok] {len(items)} entries")
    mapping: dict[str, dict] = {}

    for item in items:
        sites = item.get("sites", [])
        bangumi_id = None
        mikan_id = None
        tmdb_id = None
        tmdb_season = None
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
        if bangumi_id:
            title_trans = item.get("titleTranslate", {})
            zh_hans = title_trans.get("zh-Hans", [])
            name = zh_hans[0] if zh_hans else item.get("title", "")
            name_original = item.get("title", "")
            entry = {
                "name": name,
                "name_original": name_original,
                "mikan_id": int(mikan_id) if mikan_id else None,
            }
            if tmdb_id:
                entry["tmdb_id"] = tmdb_id
            if tmdb_season:
                entry["tmdb_season"] = tmdb_season
            mapping[bangumi_id] = entry

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"   [ok] Mapped: {len(mapping)} Bangumi entries")
    print(f"   [ok] Written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
