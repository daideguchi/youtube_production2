from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from factory_common.paths import repo_root as ssot_repo_root

router = APIRouter(prefix="/api/gh-releases-archive", tags=["archives"])

REPO_ROOT = ssot_repo_root()
ARCHIVE_DIR = REPO_ROOT / "gh_releases_archive"
MANIFEST_PATH = ARCHIVE_DIR / "manifest" / "manifest.jsonl"
LATEST_INDEX_PATH = ARCHIVE_DIR / "index" / "latest.json"

_MANIFEST_CACHE: List[Dict[str, Any]] | None = None
_MANIFEST_CACHE_MTIME: float | None = None
_MANIFEST_CACHE_AT: float | None = None
_MANIFEST_CACHE_TTL_SEC = 3.0


class GhReleasesArchiveStatus(BaseModel):
    archive_dir: str = Field(..., description="Repo-local gh_releases_archive directory")
    manifest_path: str = Field(..., description="Manifest JSONL path")
    latest_index_path: str = Field(..., description="Latest index JSON path")
    manifest_exists: bool
    latest_index_exists: bool
    manifest_entry_count: int = Field(..., description="Best-effort parsed entry count")
    latest_index_count: int = Field(..., description="Best-effort parsed latest.json count")


class GhReleasesArchiveSearchResponse(BaseModel):
    query: str
    tag: str
    offset: int
    limit: int
    total: int
    items: List[Dict[str, Any]]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _iter_manifest_entries(manifest_path: Path) -> Iterator[Dict[str, Any]]:
    if not manifest_path.exists():
        return
    with manifest_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = (line or "").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def _load_manifest_items() -> List[Dict[str, Any]]:
    global _MANIFEST_CACHE, _MANIFEST_CACHE_MTIME, _MANIFEST_CACHE_AT
    if not MANIFEST_PATH.exists():
        return []
    try:
        mtime = float(MANIFEST_PATH.stat().st_mtime)
    except Exception:
        mtime = None  # type: ignore[assignment]

    now = time.time()
    if (
        _MANIFEST_CACHE is not None
        and _MANIFEST_CACHE_AT is not None
        and now - _MANIFEST_CACHE_AT < _MANIFEST_CACHE_TTL_SEC
        and (_MANIFEST_CACHE_MTIME == mtime)
    ):
        return _MANIFEST_CACHE

    items = list(_iter_manifest_entries(MANIFEST_PATH))
    _MANIFEST_CACHE = items
    _MANIFEST_CACHE_MTIME = mtime
    _MANIFEST_CACHE_AT = now
    return items


def _slim_for_ui(item: Dict[str, Any]) -> Dict[str, Any]:
    original = item.get("original") if isinstance(item.get("original"), dict) else {}
    return {
        "archive_id": item.get("archive_id"),
        "created_at": item.get("created_at"),
        "repo": item.get("repo"),
        "release_tag": item.get("release_tag"),
        "original_name": (original or {}).get("name"),
        "original_size_bytes": (original or {}).get("size_bytes"),
        "original_sha256": (original or {}).get("sha256"),
        "tags": item.get("tags") or [],
        "note": item.get("note") or "",
    }


def _match_entry(entry: Dict[str, Any], *, query: str, tag: str) -> bool:
    if tag:
        tags = entry.get("tags") or []
        if tag not in tags:
            return False
    if not query:
        return True
    q = query.lower()
    original = entry.get("original") if isinstance(entry.get("original"), dict) else {}
    hay = " ".join(
        [
            str(entry.get("archive_id") or ""),
            str((original or {}).get("name") or ""),
            str(entry.get("note") or ""),
            ",".join([str(t) for t in (entry.get("tags") or [])]),
        ]
    ).lower()
    return q in hay


def _sorted_desc_by_created_at(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(list(items), key=lambda x: str(x.get("created_at") or ""), reverse=True)


@router.get("/status", response_model=GhReleasesArchiveStatus)
def get_archive_status():
    latest_items: List[Dict[str, Any]] = []
    if LATEST_INDEX_PATH.exists():
        obj = _read_json(LATEST_INDEX_PATH)
        if isinstance(obj, list):
            latest_items = [x for x in obj if isinstance(x, dict)]

    manifest_items = _load_manifest_items()
    return GhReleasesArchiveStatus(
        archive_dir=str(ARCHIVE_DIR),
        manifest_path=str(MANIFEST_PATH),
        latest_index_path=str(LATEST_INDEX_PATH),
        manifest_exists=MANIFEST_PATH.exists(),
        latest_index_exists=LATEST_INDEX_PATH.exists(),
        manifest_entry_count=len(manifest_items),
        latest_index_count=len(latest_items),
    )


@router.get("/latest")
def get_latest_archives(
    limit: int = Query(200, ge=1, le=1000, description="Max items to return (default: 200)."),
):
    if LATEST_INDEX_PATH.exists():
        obj = _read_json(LATEST_INDEX_PATH)
        if isinstance(obj, list):
            items = [x for x in obj if isinstance(x, dict)]
            return items[:limit]
    items = _sorted_desc_by_created_at(_load_manifest_items())
    return [_slim_for_ui(x) for x in items[:limit]]


@router.get("/search", response_model=GhReleasesArchiveSearchResponse)
def search_archives(
    query: str = Query("", description="Substring match across archive_id/original_name/tags/note."),
    tag: str = Query("", description="Exact tag match (e.g., type:episode_asset_pack)."),
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=200),
):
    query = str(query or "").strip()
    tag = str(tag or "").strip()
    items = _sorted_desc_by_created_at(_load_manifest_items())
    hits = [x for x in items if _match_entry(x, query=query, tag=tag)]
    sliced = hits[int(offset) : int(offset) + int(limit)]
    return GhReleasesArchiveSearchResponse(
        query=query,
        tag=tag,
        offset=int(offset),
        limit=int(limit),
        total=len(hits),
        items=[_slim_for_ui(x) for x in sliced],
    )


@router.get("/entry/{archive_id}")
def get_archive_entry(archive_id: str):
    aid = str(archive_id or "").strip()
    if not aid:
        raise HTTPException(status_code=400, detail="archive_id is required.")
    for entry in _load_manifest_items():
        if str(entry.get("archive_id") or "") == aid:
            return entry
    raise HTTPException(status_code=404, detail=f"archive_id not found: {aid}")


@router.get("/tags")
def list_archive_tags():
    counts: Dict[str, int] = {}
    for entry in _load_manifest_items():
        tags = entry.get("tags") or []
        for t in tags:
            tag = str(t)
            if not tag:
                continue
            counts[tag] = counts.get(tag, 0) + 1
    items = [{"tag": tag, "count": count} for tag, count in counts.items()]
    items.sort(key=lambda x: (-int(x.get("count") or 0), str(x.get("tag") or "")))
    return {"items": items}

