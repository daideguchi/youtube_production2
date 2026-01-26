from __future__ import annotations

from pathlib import Path
from typing import Optional

from factory_common.paths import repo_root

PROJECT_ROOT = repo_root()

def safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def safe_relative_path(path: Path) -> Optional[str]:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path) if safe_exists(path) else None
