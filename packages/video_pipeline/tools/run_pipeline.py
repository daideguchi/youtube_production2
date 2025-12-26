#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

def _bootstrap_repo_root() -> Path:
    start = Path(__file__).resolve()
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur


_BOOTSTRAP_REPO = _bootstrap_repo_root()
_PACKAGES_ROOT = _BOOTSTRAP_REPO / "packages"
for p in (_BOOTSTRAP_REPO, _PACKAGES_ROOT):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

from factory_common.paths import video_pkg_root  # noqa: E402

PROJECT_ROOT = video_pkg_root()

# Import using the installed package structure
try:
    from video_pipeline.src.srt2images.orchestration.config import get_args
    from video_pipeline.src.srt2images.orchestration.pipeline import run_pipeline
except ImportError:
    # Fallback to relative import if the package isn't properly installed
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    sys.path.insert(0, str(PROJECT_ROOT))
    from srt2images.orchestration.config import get_args
    from srt2images.orchestration.pipeline import run_pipeline

def main():
    args = get_args()
    run_pipeline(args)

if __name__ == "__main__":
    main()
