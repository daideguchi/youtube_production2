from __future__ import annotations

from pathlib import Path
from typing import Optional

from factory_common.paths import repo_root

PROJECT_ROOT = repo_root()


def safe_relative_path(path: Path) -> Optional[str]:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path) if path.exists() else None

