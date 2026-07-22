#!/usr/bin/env python3
"""Bump version across all project files.

Usage:
    python scripts/bump_version.py 1.2.0
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def bump_init(src: Path, new: str):
    major, minor, patch = (int(x) for x in new.split("."))
    text = src.read_text(encoding="utf-8")
    text = re.sub(r'__version__\s*=\s*"[^"]*"', f'__version__ = "{new}"', text)
    text = re.sub(r'__version_info__\s*=\s*\([^)]*\)', f'__version_info__ = ({major}, {minor}, {patch})', text)
    src.write_text(text, encoding="utf-8")
    print(f"  [OK] {src.relative_to(ROOT)}")


def bump_pyproject(src: Path, new: str):
    text = src.read_text(encoding="utf-8")
    text = re.sub(r'^version\s*=\s*"[^"]*"', f'version = "{new}"', text, flags=re.MULTILINE)
    src.write_text(text, encoding="utf-8")
    print(f"  [OK] {src.relative_to(ROOT)}")


def bump_package_json(src: Path, new: str):
    data = json.loads(src.read_text(encoding="utf-8"))
    data["version"] = new
    src.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  [OK] {src.relative_to(ROOT)}")


def bump_dockerfile(src: Path, new: str):
    text = src.read_text(encoding="utf-8")
    version_line = f'LABEL org.opencontainers.image.version="{new}"'
    if re.search(r'^LABEL\s+org\.opencontainers', text, re.MULTILINE):
        text = re.sub(r'^LABEL\s+org\.opencontainers\.image\.version="[^"]*"',
                      version_line, text, flags=re.MULTILINE)
    else:
        # Insert after FROM line
        text = re.sub(r'(^FROM .*\n)', rf'\1{version_line}\n', text, flags=re.MULTILINE)
    src.write_text(text, encoding="utf-8")
    print(f"  [OK] {src.relative_to(ROOT)}")


def main():
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <X.Y.Z>")
        sys.exit(1)

    new = sys.argv[1]
    if not re.match(r"^\d+\.\d+\.\d+$", new):
        print(f"Error: version must be X.Y.Z format, got '{new}'")
        sys.exit(1)

    print(f"Bumping version to {new}:")

    bump_init(ROOT / "my_anime_manager" / "__init__.py", new)
    bump_pyproject(ROOT / "pyproject.toml", new)
    bump_package_json(ROOT / "frontend" / "package.json", new)
    bump_dockerfile(ROOT / "Dockerfile", new)

    print(f"Done. Version: {new}")


if __name__ == "__main__":
    main()
