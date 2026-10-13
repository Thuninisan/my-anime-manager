"""Install a hardlink using the existing staged atomic replacement strategy."""

import os
from pathlib import Path


def install_hardlink(source: Path, target: Path) -> None:
    staged = target.with_name(target.name + ".mam-new")
    staged.unlink(missing_ok=True)
    os.link(source, staged)
    os.replace(staged, target)
