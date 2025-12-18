"""
tools/ execution bootstrap.

When Python is launched with a file under `tools/` (e.g. `python3 tools/foo.py`),
the interpreter's initial sys.path does NOT include the repo root. This helper:
  - Resolves repo root by searching for `pyproject.toml` (with env overrides)
  - Adds repo root and `packages/` to sys.path for monorepo imports
  - Fail-soft loads `.env` from repo root (and optional `~/.env`)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_env_files(paths: list[Path]) -> None:
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
            continue


def find_repo_root(start: Path | None = None) -> Path:
    override = os.getenv("YTM_REPO_ROOT") or os.getenv("YTM_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    if start is None:
        start = Path(__file__).resolve()
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


def ensure_monorepo_imports(start: Path | None = None) -> Path:
    repo_root = find_repo_root(start)
    packages_root = repo_root / "packages"

    for p in (repo_root, packages_root):
        p_str = str(p)
        if p_str not in sys.path:
            sys.path.insert(0, p_str)

    _load_env_files([repo_root / ".env", Path.home() / ".env"])
    return repo_root

