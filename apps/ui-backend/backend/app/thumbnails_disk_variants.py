from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from factory_common.paths import thumbnails_root as ssot_thumbnails_root

from backend.app.thumbnails_constants import THUMBNAIL_SUPPORTED_EXTENSIONS
from backend.app.thumbnails_variant_models import ThumbnailVariantResponse

THUMBNAIL_ASSETS_DIR = ssot_thumbnails_root() / "assets"


def _coerce_video_from_dir(name: str) -> Optional[str]:
    if not name:
        return None
    match = re.match(r"(\d+)", name.strip())
    if not match:
        return None
    return match.group(1).zfill(3)


def _thumbnail_asset_roots(channel_code: str) -> List[Path]:
    # Canonical root: workspaces/thumbnails/assets/{CH}/
    # (Do not scan package channel dirs; avoid legacy multi-root ambiguity.)
    return [THUMBNAIL_ASSETS_DIR / channel_code]


def _collect_disk_thumbnail_variants(channel_code: str) -> Dict[str, List[ThumbnailVariantResponse]]:
    variant_map: Dict[str, List[ThumbnailVariantResponse]] = {}
    seen_paths: set[str] = set()
    for root in _thumbnail_asset_roots(channel_code):
        if not root.exists():
            continue
        for video_dir in root.iterdir():
            if not video_dir.is_dir():
                continue
            video_number = _coerce_video_from_dir(video_dir.name)
            if not video_number:
                continue
            # IMPORTANT: do not rglob() here.
            # On Vault/shared storage (SMB), rglob() over compiler intermediates can take minutes
            # and makes `/api/workspaces/thumbnails` unusable. We only need top-level assets.
            try:
                entries = sorted(video_dir.iterdir())
            except FileNotFoundError:
                continue
            for asset_path in entries:
                if not asset_path.is_file():
                    continue
                suffix = asset_path.suffix.lower()
                if suffix not in THUMBNAIL_SUPPORTED_EXTENSIONS:
                    continue
                try:
                    rel_asset = asset_path.relative_to(video_dir)
                except ValueError:
                    rel_asset = Path(asset_path.name)
                public_rel = (Path(channel_code) / video_number / rel_asset).as_posix()
                if public_rel in seen_paths:
                    continue
                seen_paths.add(public_rel)
                label = rel_asset.as_posix()
                if suffix:
                    label = label[: -len(suffix)]
                label = label or asset_path.stem
                timestamp = datetime.fromtimestamp(asset_path.stat().st_mtime, timezone.utc).isoformat()
                digest = hashlib.sha1(public_rel.encode("utf-8")).hexdigest()[:12]
                asset_url = f"/thumbnails/assets/{public_rel}"
                variant = ThumbnailVariantResponse(
                    id=f"fs::{digest}",
                    label=label,
                    status="draft",
                    image_url=asset_url,
                    image_path=public_rel,
                    preview_url=asset_url,
                    notes=None,
                    tags=None,
                    is_selected=False,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                variant_map.setdefault(video_number, []).append(variant)
    return variant_map
