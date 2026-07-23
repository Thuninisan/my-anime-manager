#!/usr/bin/env python3
"""Split api/__init__.py routes into separate modules with APIRouter."""
import re
from pathlib import Path

API = Path("my_anime_manager/api")
INIT = API / "__init__.py"
text = INIT.read_text(encoding="utf-8")
lines = text.split("\n")

# ── Route group definitions: (filename, start_line_keyword, end_line_keyword) ──
# Keywords are exact strings to match in comment lines
GROUPS = [
    ("torrent", "# ── /api/torrent/subtitle", "# ── /scan ──"),
    ("system",  "# ── /scan ──",            "# ── /config ──"),
    ("settings","# ── /config ──",           "# ── /api/rss/bangumi/{id} ──"),
    ("rss",     "# ── /api/rss/bangumi",     "# ── # ═"),
    ("tmdb",    "# ── /api/rss/tmdb-search", None),  # till next group or end
    ("downloader","# ── /api/rss/downloader", None),
    ("history", "# ── /api/rss/subscriptions/{id}/history", "# ── /api/rss/download-history/{id}/{sort}"),
]

# Find exact line numbers for each group
group_ranges = {}
for name, start_kw, end_kw in GROUPS:
    s = e = -1
    for i, l in enumerate(lines):
        if s < 0 and l.strip().startswith(start_kw):
            s = i - 1  # include the blank line before
            while s > 0 and not lines[s-1].strip():
                s -= 1
        if e < 0 and end_kw and l.strip().startswith(end_kw) and i > s:
            e = i
    if e < 0:  # no end keyword, find next route section or end of file
        for i in range(s + 1, len(lines)):
            if re.match(r'@app\.(get|post|put|patch|delete)\(', lines[i]) and i > s + 10:
                e = i - 1
                while e > s and not lines[e].strip():
                    e -= 1
                break
        if e < 0:
            e = len(lines)
    if s >= 0 and e > s:
        group_ranges[name] = (s, e)
        print(f"  {name}: lines {s+1}-{e+1} ({e-s} lines)")

# ── Write each route file ──
ROUTE_TEMPLATE = '''"""API routes: {name}."""

from fastapi import APIRouter{extra_imports}

router = APIRouter()

'''

for name, (s, e) in group_ranges.items():
    code_lines = lines[s:e]

    # Determine extra imports needed
    extra = ""
    code = "\n".join(code_lines)

    # Change @app.xxx to @router.xxx
    code = re.sub(r'@app\.(get|post|put|patch|delete)\(', r'@router.\1(', code)

    out_path = API / f"routes_{name}.py"
    content = ROUTE_TEMPLATE.format(name=name, extra_imports=extra) + code + "\n"
    out_path.write_text(content, encoding="utf-8")
    print(f"  Wrote {out_path} ({len(content.splitlines())} lines)")

print("\nDone! Check files compile with: python -m py_compile api/routes_*.py")
