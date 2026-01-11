from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, Optional

from factory_common.paths import thumbnails_root as ssot_thumbnails_root

THUMBNAIL_QC_NOTES_PATH = ssot_thumbnails_root() / "qc_notes.json"
THUMBNAIL_QC_NOTES_LOCK = threading.Lock()


def is_thumbnail_qc_relative_path(relative_path: str) -> bool:
    rel = (relative_path or "").strip().replace("\\", "/")
    if not rel:
        return False
    if rel.startswith("/") or rel.startswith("../") or "/../" in rel:
        return False
    if rel.startswith("_qc/") or rel.startswith("library/qc/") or rel.startswith("qc/"):
        return True
    name = Path(rel).name
    return name.startswith("qc__") or name.startswith("contactsheet")


def load_thumbnail_qc_notes_document(*, path: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    path = path or THUMBNAIL_QC_NOTES_PATH
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for channel_code, raw_notes in payload.items():
        if not isinstance(channel_code, str) or not isinstance(raw_notes, dict):
            continue
        normalized_channel = channel_code.strip().upper()
        if not normalized_channel:
            continue
        notes: Dict[str, str] = {}
        for rel, note in raw_notes.items():
            if not isinstance(rel, str) or not isinstance(note, str):
                continue
            rel_norm = rel.strip().replace("\\", "/")
            note_norm = note.strip()
            if not rel_norm or not note_norm:
                continue
            notes[rel_norm] = note_norm
        if notes:
            out[normalized_channel] = notes
    return out


def write_thumbnail_qc_notes_document(
    notes_by_channel: Dict[str, Dict[str, str]],
    *,
    path: Optional[Path] = None,
) -> None:
    path = path or THUMBNAIL_QC_NOTES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Dict[str, str]] = {}
    for channel_code, notes in notes_by_channel.items():
        if not isinstance(channel_code, str) or not isinstance(notes, dict):
            continue
        normalized_channel = channel_code.strip().upper()
        if not normalized_channel:
            continue
        cleaned: Dict[str, str] = {}
        for rel, note in notes.items():
            if not isinstance(rel, str) or not isinstance(note, str):
                continue
            rel_norm = rel.strip().replace("\\", "/")
            note_norm = note.strip()
            if not rel_norm or not note_norm:
                continue
            cleaned[rel_norm] = note_norm
        if cleaned:
            payload[normalized_channel] = dict(sorted(cleaned.items(), key=lambda item: item[0]))

    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
