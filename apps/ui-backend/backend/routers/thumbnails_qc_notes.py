from __future__ import annotations

from pathlib import Path
from typing import Dict

from fastapi import APIRouter, HTTPException

from backend.core.tools import thumbnails_qc_notes as qc_notes
from backend.main import ThumbnailQcNoteUpdateRequest
from backend.routers.ssot_docs import normalize_channel_code
from factory_common.paths import thumbnails_root as ssot_thumbnails_root

router = APIRouter(prefix="/api/workspaces/thumbnails", tags=["thumbnails"])

THUMBNAIL_ASSETS_DIR = ssot_thumbnails_root() / "assets"


@router.get(
    "/{channel}/qc-notes",
    response_model=Dict[str, str],
)
def get_thumbnail_qc_notes(channel: str):
    channel_code = normalize_channel_code(channel)
    with qc_notes.THUMBNAIL_QC_NOTES_LOCK:
        document = qc_notes.load_thumbnail_qc_notes_document()
    return document.get(channel_code, {})


@router.put(
    "/{channel}/qc-notes",
    response_model=Dict[str, str],
)
def upsert_thumbnail_qc_note(channel: str, payload: ThumbnailQcNoteUpdateRequest):
    channel_code = normalize_channel_code(channel)
    rel = (payload.relative_path or "").strip().replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    if not qc_notes.is_thumbnail_qc_relative_path(rel):
        raise HTTPException(status_code=400, detail="invalid QC relative_path")

    base_dir = (THUMBNAIL_ASSETS_DIR / channel_code).resolve()
    candidate = (THUMBNAIL_ASSETS_DIR / channel_code / rel).resolve()
    try:
        candidate.relative_to(base_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid relative_path") from exc
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="QC asset not found")

    note = (payload.note or "").strip()
    with qc_notes.THUMBNAIL_QC_NOTES_LOCK:
        document = qc_notes.load_thumbnail_qc_notes_document()
        channel_notes = dict(document.get(channel_code, {}))
        if note:
            channel_notes[rel] = note
        else:
            channel_notes.pop(rel, None)
        if channel_notes:
            document[channel_code] = channel_notes
        else:
            document.pop(channel_code, None)
        qc_notes.write_thumbnail_qc_notes_document(document)
        return document.get(channel_code, {})

