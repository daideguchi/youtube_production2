from __future__ import annotations

from pathlib import Path
from typing import Iterable, List


# Repo layout SSOT: `ssot/ops/OPS_REPO_DIRECTORY_SSOT.md`
ALLOWED_REPO_ROOT_DIR_NAMES = frozenset(
    {
        "apps",
        "asset",
        "backups",
        "configs",
        "credentials",
        "data",
        "docs",
        "packages",
        "prompts",
        "scripts",
        "ssot",
        "tests",
        "workspaces",
    }
)

# These directories can appear transiently during local development and are not treated as layout drift.
IGNORED_REPO_ROOT_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)


def unexpected_repo_root_entries(repo_root: Path, *, allow_extra: Iterable[str] = ()) -> List[Path]:
    """
    Return unexpected repo-root entries that create "迷いどころ" / path drift.

    Policy:
    - Allowed: known top-level directories (SSOT)
    - Ignored: transient caches
    - Unexpected:
        - any symlink at repo root (compat aliases are forbidden)
        - any directory at repo root not in the allowlist

    Notes:
    - Files are intentionally ignored (pyproject.toml, README.md, etc).
    - This is used by audits/tests/scripts to prevent reintroducing legacy alias directories.
    """
    allowed = set(ALLOWED_REPO_ROOT_DIR_NAMES)
    allowed.update(str(x) for x in allow_extra)

    unexpected: List[Path] = []
    for p in repo_root.iterdir():
        name = p.name
        if name.startswith("."):
            continue
        if name in IGNORED_REPO_ROOT_DIR_NAMES:
            continue
        if p.is_symlink():
            unexpected.append(p)
            continue
        if p.is_dir() and name not in allowed:
            unexpected.append(p)

    unexpected.sort(key=lambda x: x.name)
    return unexpected


__all__ = [
    "ALLOWED_REPO_ROOT_DIR_NAMES",
    "IGNORED_REPO_ROOT_DIR_NAMES",
    "unexpected_repo_root_entries",
]
