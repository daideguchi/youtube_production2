from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Optional

from factory_common.paths import (
    capcut_draft_root,
    repo_root,
    video_capcut_local_drafts_root,
    workspace_root,
)


PATH_REF_SCHEMA_V1 = "ytm.path_ref.v1"


def is_path_ref(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return str(value.get("schema") or "").strip() == PATH_REF_SCHEMA_V1


def _clean_rel(rel: str) -> Optional[str]:
    r = str(rel or "").strip()
    if not r:
        return None
    r = r.lstrip("/")
    parts = PurePosixPath(r).parts
    if not parts:
        return None
    if any(p in ("", ".", "..") for p in parts):
        return None
    return str(PurePosixPath(*parts))


def root_path_for_key(root: str) -> Optional[Path]:
    key = str(root or "").strip()
    if not key:
        return None
    if key == "repo":
        return repo_root()
    if key == "workspace":
        return workspace_root()
    if key == "capcut_draft_root":
        return capcut_draft_root()
    if key == "capcut_fallback_root":
        return video_capcut_local_drafts_root()
    return None


def resolve_path_ref(ref: Any) -> Optional[Path]:
    if not is_path_ref(ref):
        return None
    root_key = str(ref.get("root") or "").strip()
    rel = _clean_rel(str(ref.get("rel") or ""))
    if not root_key or not rel:
        return None
    root_path = root_path_for_key(root_key)
    if root_path is None:
        return None

    root_path = root_path.expanduser()
    if not root_path.is_absolute():
        # Best-effort: interpret relative roots as repo-root relative.
        root_path = repo_root() / root_path

    parts = PurePosixPath(rel).parts
    return root_path.joinpath(*parts)


def path_ref_from_path(path: Path, *, root_key: str, root_path: Path) -> Optional[dict[str, str]]:
    p = Path(path).expanduser()
    r = Path(root_path).expanduser()
    if not p.is_absolute():
        p = repo_root() / p
    if not r.is_absolute():
        r = repo_root() / r
    try:
        rel = p.relative_to(r)
    except Exception:
        return None
    rel_str = _clean_rel(rel.as_posix())
    if not rel_str:
        return None
    return {"schema": PATH_REF_SCHEMA_V1, "root": str(root_key), "rel": rel_str}


def best_effort_path_ref(path: Path) -> Optional[dict[str, str]]:
    """
    Create a PathRef for a concrete path, choosing the most specific known root.
    Returns None if the path doesn't fit any known root.
    """
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = repo_root() / p

    # Prefer semantic roots first (capcut fallback vs generic workspace).
    candidates: list[tuple[str, Path]] = [
        ("capcut_fallback_root", video_capcut_local_drafts_root()),
        ("capcut_draft_root", capcut_draft_root()),
        ("workspace", workspace_root()),
        ("repo", repo_root()),
    ]
    for key, root in candidates:
        ref = path_ref_from_path(p, root_key=key, root_path=root)
        if ref:
            return ref
    return None

