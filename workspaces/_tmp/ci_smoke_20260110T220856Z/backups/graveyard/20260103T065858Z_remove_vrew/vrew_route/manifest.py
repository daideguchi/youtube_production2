from __future__ import annotations

import hashlib
from typing import Any, Dict


def compute_prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def segment_id(queue_index: int) -> str:
    return f"seg_{queue_index:04d}"


def build_manifest(
    *,
    project_id: str,
    source_type: str,
    image_spec: Dict[str, Any],
    segments: list[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "project_id": project_id,
        "source_type": source_type,
        "image_spec": image_spec,
        "segments": segments,
    }

