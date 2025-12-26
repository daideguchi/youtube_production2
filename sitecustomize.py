"""
Global .env loader.
Always load environment variables from the project root .env
before any application code runs.

Also ensures `packages/` is importable so top-level imports like `factory_common`
work without root-level symlinks.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def load_env_files(paths: list[Path]) -> None:
    for env_path in paths:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                k, v = stripped.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                os.environ.setdefault(k, v)
        except Exception:
            # Fail-soft: do not crash python startup because of env parse errors
            continue


# Project root is assumed to be two levels up from this file
PROJECT_ROOT = Path(__file__).resolve().parent
PACKAGES_ROOT = PROJECT_ROOT / "packages"

# Keep `packages/` importable for monorepo layout.
if PACKAGES_ROOT.exists():
    packages_str = str(PACKAGES_ROOT)
    if packages_str not in sys.path:
        sys.path.insert(0, packages_str)

env_candidates = [
    PROJECT_ROOT / ".env",
]

load_env_files(env_candidates)
