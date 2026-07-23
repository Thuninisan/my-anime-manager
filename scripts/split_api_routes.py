#!/usr/bin/env python3
"""Extract route groups from api/__init__.py into separate modules using APIRouter."""
import re
from pathlib import Path

API_DIR = Path("my_anime_manager/api")
INIT = API_DIR / "__init__.py"

text = INIT.read_text(encoding="utf-8")
lines = text.split("\n")

# ── Find all @app.route decorated functions and their line ranges ──
route_funcs = []
for i, line in enumerate(lines):
    m = re.match(r'@app\.(get|post|put|patch|delete)\(', line)
    if m:
        # Find the function definition (next non-decorator line starting with async def or def)
        start = i
        for j in range(i+1, min(i+5, len(lines))):
            if re.match(r'(async )?def ', lines[j]):
                func_name = lines[j].split("(")[0].split()[-1]
                # Find end of function (next @app. or top-level definition)
                end = j + 1
                while end < len(lines):
                    if re.match(r'@app\.|^# ═', lines[end]) and lines[end-1].strip() == "":
                        break
                    end += 1
                route_funcs.append({
                    "method": m.group(1),
                    "path": m.group(0).split('"')[1] if '"' in m.group(0) else m.group(0).split("'")[1],
                    "func": func_name,
                    "line_start": start,
                    "line_end": end,
                })
                break

# ── Group routes by prefix ──
GROUPS = {
    "system": [
        ("/scan", "scan"),
        ("/scan/status", "scan_status"),
        ("/watch/status", "watch_status"),
        ("/api/update/check", "check_update"),
        ("/api/update/apply", "apply_update"),
        ("/{full_path:path}", "serve_frontend"),
    ],
}

# Group routes
route_by_name = {r["func"]: r for r in route_funcs}
print(f"Found {len(route_funcs)} route functions")

# ── Count lines that would be saved by extracting each group ──
for group_name, routes in GROUPS.items():
    total = 0
    for path, func in routes:
        r = route_by_name.get(func)
        if r:
            total += r["line_end"] - r["line_start"]
    print(f"  {group_name}: {len(routes)} routes, ~{total} lines")

print(f"\nTotal routes with prefixes:")
prefixes = {}
for r in route_funcs:
    pfx = "/".join(r["path"].split("/")[:3])
    prefixes.setdefault(pfx, []).append(r["func"])
for pfx, funcs in sorted(prefixes.items()):
    print(f"  {pfx}: {len(funcs)} routes - {', '.join(funcs[:3])}")
