from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from factory_common.paths import repo_root as ssot_repo_root

REPO_ROOT = ssot_repo_root()
# NOTE: PROJECT_ROOT is treated as repo-root throughout the UI backend (legacy alias).
PROJECT_ROOT = REPO_ROOT


def normalize_audio_path_string(value: str) -> str:
    if not value:
        return value
    path_obj = Path(value)
    if path_obj.is_absolute():
        try:
            return str(path_obj.relative_to(PROJECT_ROOT))
        except ValueError:
            return value
    return value


def normalize_audio_metadata(metadata: Optional[dict]) -> Optional[dict]:
    if not isinstance(metadata, dict):
        return None

    def _transform(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {key: _transform(val) for key, val in obj.items()}
        if isinstance(obj, list):
            return [_transform(item) for item in obj]
        if isinstance(obj, str):
            return normalize_audio_path_string(obj)
        return obj

    return _transform(metadata)

