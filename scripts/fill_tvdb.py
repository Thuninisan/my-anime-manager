"""Fill TVDB fields in docs/subscriptions_new.json from the SQLite mapping database."""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

SUBS_PATH = os.path.join(PROJECT_DIR, "docs", "subscriptions_new.json")
sys.path.insert(0, PROJECT_DIR)
from backend.db.legacy_data import list_mappings


def main():
    with open(SUBS_PATH, "r", encoding="utf-8") as f:
        subs = json.load(f)

    bmm = list_mappings()

    filled = 0
    skipped = 0

    for s in subs:
        bid = int(s["bangumi_id"])
        entry = bmm.get(bid, {})
        tid = entry.get("tvdb_id")
        tseason = entry.get("tvdb_season")

        if tid is not None:
            s["tvdb"]["id"] = tid
            s["tvdb"]["season"] = tseason
            filled += 1
            status = f"tvdb_id={tid}, tvdb_season={tseason}"
        else:
            skipped += 1
            status = "no TVDB in map"

        print(f"  {s['name']:30s} (bgm={s['bangumi_id']:>6d}) -> {status}")

    with open(SUBS_PATH, "w", encoding="utf-8") as f:
        json.dump(subs, f, ensure_ascii=False, indent=2)

    print(f"\nDone: {filled} filled, {skipped} skipped")


if __name__ == "__main__":
    main()
