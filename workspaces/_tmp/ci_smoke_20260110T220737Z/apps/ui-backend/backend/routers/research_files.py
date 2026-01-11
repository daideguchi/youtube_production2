from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

from fastapi import APIRouter, HTTPException, Query

from factory_common.paths import (
    audio_artifacts_root,
    logs_root,
    planning_root,
    repo_root,
    research_root,
    script_data_root,
    thumbnails_root,
    video_runs_root,
)

router = APIRouter(prefix="/api/research", tags=["research"])

BASE_DIRS: Dict[str, Path] = {
    "research": research_root(),
    "scripts": script_data_root(),
    "ssot": repo_root() / "ssot",
    # Repo code/prompt/config browsing (read-only; repo-root itself is intentionally NOT exposed).
    "packages": repo_root() / "packages",
    "backend": repo_root() / "apps" / "ui-backend" / "backend",
    "frontend": repo_root() / "apps" / "ui-frontend" / "src",
    "repo_scripts": repo_root() / "scripts",
    "prompts": repo_root() / "prompts",
    "configs": repo_root() / "configs",
    "tests": repo_root() / "tests",
    # Workspaces (generated SoT/artifacts) browsing.
    "planning": planning_root(),
    "audio": audio_artifacts_root(),
    "video_runs": video_runs_root(),
    "thumbnails": thumbnails_root(),
    "logs": logs_root(),
}


def _resolve_path(base: str, rel: str) -> Tuple[Path, Path]:
    base_dir = BASE_DIRS.get(base)
    if not base_dir:
        raise HTTPException(status_code=400, detail="invalid base")
    normalized_base = base_dir.resolve()
    target = (normalized_base / rel).resolve()
    try:
        target.relative_to(normalized_base)
    except Exception:
        raise HTTPException(status_code=400, detail="path is outside allowed base")
    return normalized_base, target


@router.get("/list")
def list_research_files(
    base: str = Query(
        "research",
        description="research | scripts | ssot | packages | backend | frontend | repo_scripts | prompts | configs | tests | planning | audio | video_runs | thumbnails | logs",
    ),
    path: str = Query("", description="relative path from base"),
):
    base_dir, target = _resolve_path(base, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="path not found")

    listing_target = target if target.is_dir() else target.parent
    entries = []
    for child in sorted(listing_target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        stat = child.stat()
        entries.append(
            {
                "name": child.name,
                "path": str(child.relative_to(base_dir)),
                "is_dir": child.is_dir(),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    rel_path = str(listing_target.relative_to(base_dir))
    if rel_path == ".":
        rel_path = ""
    return {
        "base": base,
        "path": rel_path,
        "entries": entries,
    }


@router.get("/file")
def read_research_file(
    base: str = Query(
        "research",
        description="research | scripts | ssot | packages | backend | frontend | repo_scripts | prompts | configs | tests | planning | audio | video_runs | thumbnails | logs",
    ),
    path: str = Query(..., description="relative file path from base"),
    offset: int | None = Query(None, ge=0, description="0-based line offset (when set, returns a partial preview)"),
    length: int | None = Query(None, ge=1, le=5000, description="max lines to return (when set, returns a partial preview)"),
):
    base_dir, target = _resolve_path(base, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    stat = target.stat()
    is_partial = offset is not None or length is not None
    if not is_partial and stat.st_size > 800_000:
        raise HTTPException(
            status_code=400,
            detail="file too large to preview (limit 800KB). Use offset/length for partial view.",
        )

    if is_partial:
        start = int(offset or 0)
        max_lines = int(length or 200)
        lines: list[str] = []
        try:
            with target.open("r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i < start:
                        continue
                    lines.append(line.rstrip("\n"))
                    if len(lines) >= max_lines:
                        break
        except Exception:
            raise HTTPException(status_code=415, detail="unsupported file encoding")
        content = "\n".join(lines)
    else:
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                raise HTTPException(status_code=415, detail="unsupported file encoding")
    return {
        "base": base,
        "path": str(target.relative_to(base_dir)),
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "content": content,
        "is_partial": bool(is_partial),
        "offset": offset,
        "length": length,
    }
