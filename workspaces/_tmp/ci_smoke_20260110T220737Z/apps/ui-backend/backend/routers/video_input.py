from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from factory_common.paths import video_input_root as ssot_video_input_root

router = APIRouter(prefix="/api/workspaces/video/input", tags=["workspaces"])


@router.get("/{run_id}/{asset_path:path}")
def get_video_input_asset(run_id: str, asset_path: str):
    """
    Serve run input assets from workspaces/video/input for Remotion preview.

    Expected layout:
      workspaces/video/input/<run_id>/{belt_config.json,image_cues.json,<run_id>.srt,<run_id>.wav,images/*}
    """
    run_id_clean = (run_id or "").strip()
    if not run_id_clean or Path(run_id_clean).name != run_id_clean:
        raise HTTPException(status_code=404, detail="invalid run")
    if not asset_path or asset_path.strip() == "":
        raise HTTPException(status_code=404, detail="invalid asset")
    rel_asset = Path(asset_path)
    if rel_asset.is_absolute():
        raise HTTPException(status_code=404, detail="invalid asset")
    if any(part == ".." for part in rel_asset.parts):
        raise HTTPException(status_code=404, detail="invalid asset")

    root = ssot_video_input_root() / run_id_clean
    candidate = root / rel_asset
    if not root.exists():
        raise HTTPException(status_code=404, detail="run not found")
    try:
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
        resolved_candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="invalid asset")
    if not resolved_candidate.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    media_type = mimetypes.guess_type(resolved_candidate.name)[0] or "application/octet-stream"
    return FileResponse(resolved_candidate, media_type=media_type, filename=resolved_candidate.name)

