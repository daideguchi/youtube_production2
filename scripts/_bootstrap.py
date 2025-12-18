"""
scripts/ bootstrap helpers.

This repo is frequently executed via `python3 scripts/<tool>.py` which does NOT
guarantee the repo root (or `packages/`) is on `sys.path`. Also, multiple agents
move files during refactors, so brittle `Path(__file__).parents[...]` path hacks
are forbidden (see AGENTS.md).

This module provides a single, refactor-safe way to:
  - discover repo root (by searching for `pyproject.toml`)
  - ensure `repo_root/` and `repo_root/packages/` are on `sys.path`
  - load `.env` fail-soft (does not override existing env vars)
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
            # Fail-soft: do not crash scripts due to env parse errors
            continue


def bootstrap(*, load_env: bool = True) -> Path:
    """
    Ensure repo-root imports work for scripts. Returns the resolved repo root.
    """
    override = os.getenv("YTM_REPO_ROOT") or os.getenv("YTM_ROOT")
    if override:
        repo_root = Path(override).expanduser().resolve()
    else:
        # Prefer CWD (works for most operators); fallback to this file path for robustness.
        try:
            repo_root = _discover_repo_root(Path.cwd().resolve())
        except Exception:
            repo_root = _discover_repo_root(Path(__file__).resolve())

    # Keep monorepo imports working regardless of caller CWD.
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

