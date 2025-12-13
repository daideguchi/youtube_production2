"""
scripts/ execution bootstrap.

When Python is launched with a file under `scripts/` (e.g. `python3 scripts/foo.py`),
the interpreter's initial sys.path does NOT include the repo root. That means the
root-level `sitecustomize.py` is not discovered, and imports like `factory_common`
would rely on root symlinks.

This `sitecustomize.py` is discovered because `scripts/` is on sys.path in that
execution mode, and it:
  - Adds repo root and `packages/` to sys.path (monorepo layout)
  - Loads `.env` from repo root (and optional `~/.env`) before any app code runs
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
                key, value = stripped.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        except Exception:
            # Fail-soft: do not crash python startup because of env parse errors
            continue


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_ROOT = REPO_ROOT / "packages"

for p in (REPO_ROOT, PACKAGES_ROOT):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

load_env_files([REPO_ROOT / ".env", Path.home() / ".env"])

