"""
Bootstrap helper for `video_pipeline.tools/*`.

Why this exists:
- Many tools are run in two styles:
  1) recommended: `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.<tool>`
  2) ad-hoc:     `python3 packages/video_pipeline/tools/<tool>.py`
- In (2), the repo root is not on sys.path, so importing `_bootstrap` / shared packages breaks.

This module provides a single, consistent place to:
- discover repo root (pyproject.toml search, with env override)
- ensure repo root + `packages/` are on `sys.path`
- delegate to repo-level `_bootstrap.bootstrap()` when available
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _discover_repo_root(start: Path) -> Path:
    override = os.getenv("YTM_REPO_ROOT") or os.getenv("YTM_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


def _ensure_sys_path(repo_root: Path) -> None:
    for p in (repo_root, repo_root / "packages"):
        if not p.exists():
            continue
        p_str = str(p)
        if p_str not in sys.path:
            sys.path.insert(0, p_str)
    os.environ.setdefault("YTM_REPO_ROOT", str(repo_root))


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    except Exception:
        return


def bootstrap(*, load_env: bool = False) -> Path:
    repo_root = _discover_repo_root(Path(__file__).resolve())
    _ensure_sys_path(repo_root)

    try:
        from _bootstrap import bootstrap as repo_bootstrap  # noqa: WPS433 (runtime import)

        return repo_bootstrap(load_env=load_env)
    except Exception:
        if load_env:
            _load_env_file(repo_root / ".env")
        return repo_root


__all__ = ["bootstrap"]

