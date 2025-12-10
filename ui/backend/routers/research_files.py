from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

from fastapi import APIRouter, HTTPException, Query

FILE_PATH = Path(__file__).resolve()
PROJECT_ROOT = FILE_PATH.parents[3]

router = APIRouter(prefix="/api/research", tags=["research"])

BASE_DIRS: Dict[str, Path] = {
    "research": PROJECT_ROOT / "00_research",
    "scripts": PROJECT_ROOT / "script_pipeline" / "data",
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
    base: str = Query("research", description="research | scripts"),
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
    base: str = Query("research", description="research | scripts"),
    path: str = Query(..., description="relative file path from base"),
):
    base_dir, target = _resolve_path(base, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    stat = target.stat()
    if stat.st_size > 800_000:
        raise HTTPException(status_code=400, detail="file too large to preview (limit 800KB)")
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
    }
