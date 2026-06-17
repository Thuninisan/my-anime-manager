"""Quick anitopy parse tester — type a filename, see the result.

Usage: python test_parse.py
Type filenames at the prompt, empty line to quit.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from my_anime_manager.vendor.anitopy import parse
from my_anime_manager.utils.parser import parse_filename


def show(filename: str):
    print(f"\n📁 {filename}")
    print("-" * 50)

    r = parse(filename)
    for k, v in sorted(r.items()):
        print(f"  {k}: {v!r}")

    r2 = parse_filename(filename)
    if r2:
        print(f"\n  → showName: {r2['showName']!r}  S{r2['season']:02d}E{r2['episode']:02d}")
    else:
        print(f"\n  → ⚠️ parse_filename() returned None")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for f in sys.argv[1:]:
            show(f)
    else:
        print("Enter filenames (empty line to quit):")
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                break
            show(line)
