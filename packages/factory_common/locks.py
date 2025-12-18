from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from factory_common import paths as repo_paths


@dataclass(frozen=True)
class CoordinationLock:
    lock_id: str
    created_by: str
    mode: str
    scopes: tuple[str, ...]
    expires_at: Optional[datetime]


_GLOB_CHARS = {"*", "?", "[", "]"}


def _agent_name() -> Optional[str]:
    raw = (os.getenv("LLM_AGENT_NAME") or os.getenv("AGENT_NAME") or "").strip()
    return raw or None


def _parse_dt(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        # agent_org writes "+00:00" style, but accept "Z" just in case.
        v = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except Exception:
        return None


def _queue_dir() -> Path:
    raw = (os.getenv("LLM_AGENT_QUEUE_DIR") or "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (repo_paths.repo_root() / p)
    return repo_paths.logs_root() / "agent_tasks"


def coordination_locks_dir() -> Path:
    return _queue_dir() / "coordination" / "locks"


def load_active_locks(*, ignore_created_by: Optional[str] = None) -> list[CoordinationLock]:
    """
    Load active coordination locks (best-effort).

    ignore_created_by:
      If set, locks created by that agent are ignored (useful when the caller holds a lock).
    """
    locks_dir = coordination_locks_dir()
    if not locks_dir.exists():
        return []

    now = datetime.now(timezone.utc)
    out: list[CoordinationLock] = []
    for p in sorted(locks_dir.glob("lock__*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue

        lock_id = str(data.get("id") or "").strip()
        if not lock_id:
            continue

        expires_at = _parse_dt(data.get("expires_at"))
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at and expires_at <= now:
            continue

        created_by = str(data.get("created_by") or "").strip()
        if ignore_created_by and created_by and created_by == ignore_created_by:
            continue

        mode = str(data.get("mode") or "").strip()
        scopes_raw = data.get("scopes")
        if not isinstance(scopes_raw, list):
            continue
        scopes: list[str] = []
        for s in scopes_raw:
            if isinstance(s, str) and s.strip():
                scopes.append(s.strip().replace("\\", "/"))
        out.append(
            CoordinationLock(
                lock_id=lock_id,
                created_by=created_by,
                mode=mode,
                scopes=tuple(scopes),
                expires_at=expires_at,
            )
        )
    return out


def _has_glob(pattern: str) -> bool:
    if "**" in pattern:
        return True
    return any(ch in pattern for ch in _GLOB_CHARS)


def _static_prefix(pattern: str) -> str:
    """
    Return the non-glob prefix of a pattern (best-effort).
    Used to detect "candidate is parent dir of a locked glob scope".
    """
    for i, ch in enumerate(pattern):
        if ch in _GLOB_CHARS:
            return pattern[:i]
    if "**" in pattern:
        return pattern.split("**", 1)[0]
    return pattern


def _is_same_or_parent(parent: str, child: str) -> bool:
    if parent == child:
        return True
    if not parent:
        return False
    return child.startswith(parent.rstrip("/") + "/")


def relpath_intersects_scope(relpath: str, scope: str) -> bool:
    """
    Return True when deleting/rewriting relpath could touch a locked scope.

    Both inputs are repo-relative POSIX paths (e.g. "apps/ui-frontend/src/App.tsx").
    scope may include glob wildcards.
    """
    rp = (relpath or "").strip().replace("\\", "/").strip("/")
    sc = (scope or "").strip().replace("\\", "/").strip("/")
    if not rp or not sc:
        return False

    if not _has_glob(sc):
        return _is_same_or_parent(sc, rp) or _is_same_or_parent(rp, sc)

    if fnmatch.fnmatchcase(rp, sc):
        return True

    prefix = _static_prefix(sc).rstrip("/")
    if prefix and _is_same_or_parent(rp, prefix):
        return True

    return False


def find_blocking_lock(path: Path, locks: Iterable[CoordinationLock]) -> Optional[CoordinationLock]:
    """
    Return the first lock that intersects the given path, else None.

    Note:
    - For symlinks we prefer matching on the symlink *path* (inside the repo),
      not only the resolved target (which can be outside the repo).
    - When the resolved target is also inside the repo, we check both.
    """
    repo_root = repo_paths.repo_root()
    rel_candidates: list[str] = []

    # 1) Non-resolving (symlink-safe) repo-relative path.
    try:
        base = path.absolute() if path.is_absolute() else (repo_root / path).absolute()
        rel_candidates.append(base.relative_to(repo_root).as_posix())
    except Exception:
        pass

    # 2) Resolved target path (when also inside repo).
    try:
        rel_resolved = path.resolve().relative_to(repo_root).as_posix()
        if rel_resolved not in rel_candidates:
            rel_candidates.append(rel_resolved)
    except Exception:
        pass

    if not rel_candidates:
        return None

    for rel in rel_candidates:
        for lock in locks:
            for scope in lock.scopes:
                if relpath_intersects_scope(rel, scope):
                    return lock
    return None


def default_active_locks_for_mutation() -> list[CoordinationLock]:
    """
    Default policy used by cleanup scripts:
    - Respect active locks.
    - If the current process has an agent name, ignore locks created by itself.
    """
    return load_active_locks(ignore_created_by=_agent_name())
