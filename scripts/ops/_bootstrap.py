"""
scripts/ops bootstrap helpers.

`python3 scripts/ops/<tool>.py` often runs with `sys.path[0] == scripts/ops`,
so repo-root imports may fail unless we explicitly add repo root + `packages/`.

This module mirrors `scripts/_bootstrap.py` so ops scripts can remain thin and
avoid forbidden `Path(__file__).parents[...]` path hacks.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    raise RuntimeError(
        "repo root not found (pyproject.toml). Run from inside the repo or set YTM_REPO_ROOT."
    )


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


def bootstrap(*, load_env: bool = True) -> Path:
    override = os.getenv("YTM_REPO_ROOT") or os.getenv("YTM_ROOT")
    if override:
        repo_root = Path(override).expanduser().resolve()
    else:
        try:
            repo_root = _discover_repo_root(Path.cwd().resolve())
        except Exception:
            repo_root = _discover_repo_root(Path(__file__).resolve())

    for p in (repo_root, repo_root / "packages"):
        if not p.exists():
            continue
        p_str = str(p)
        if p_str not in sys.path:
            sys.path.insert(0, p_str)

    os.environ.setdefault("YTM_REPO_ROOT", str(repo_root))

    if load_env:
        _load_env_files([repo_root / ".env", Path.home() / ".env"])

    return repo_root

