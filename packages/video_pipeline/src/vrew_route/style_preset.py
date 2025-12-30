from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


_DEFAULT_STYLE_PREFIX = (
    "和紙テクスチャ背景のデジタル水彩で薄い茶からグレーの線画と淡いゴールドの光とブルーグレーの影と金粉粒子を入れた"
)
_DEFAULT_CONSTRAINTS = "16対9の構図で文字入れなしで透かしなしで破綻しない構図にする"
_DEFAULT_BANNED_TERMS = ["ロゴ", "透かし", "文字入り", "ウォーターマーク"]
_DEFAULT_DURATION_MS = 5000
_DEFAULT_IMAGE_SPEC: Dict[str, Any] = {"width": 1920, "height": 1080, "format": "png"}


@dataclass(frozen=True)
class StylePreset:
    style_prefix: str = _DEFAULT_STYLE_PREFIX
    constraints: str = _DEFAULT_CONSTRAINTS
    banned_terms: List[str] = field(default_factory=lambda: list(_DEFAULT_BANNED_TERMS))
    default_duration_ms: int = _DEFAULT_DURATION_MS
    image_spec: Dict[str, Any] = field(default_factory=lambda: dict(_DEFAULT_IMAGE_SPEC))

    @staticmethod
    def load(path: Optional[Path]) -> "StylePreset":
        if not path:
            return StylePreset()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return StylePreset()

        style_prefix = str(data.get("style_prefix") or _DEFAULT_STYLE_PREFIX)
        constraints = str(data.get("constraints") or _DEFAULT_CONSTRAINTS)
        banned_terms_raw = data.get("banned_terms")
        banned_terms: List[str]
        if isinstance(banned_terms_raw, list):
            banned_terms = [str(x) for x in banned_terms_raw if str(x).strip()]
        else:
            banned_terms = list(_DEFAULT_BANNED_TERMS)

        default_duration_ms = int(data.get("default_duration_ms") or _DEFAULT_DURATION_MS)
        image_spec_raw = data.get("image_spec")
        image_spec = dict(_DEFAULT_IMAGE_SPEC)
        if isinstance(image_spec_raw, dict):
            image_spec.update({k: image_spec_raw.get(k) for k in ("width", "height", "format") if k in image_spec_raw})

        # Normalize image_spec
        try:
            image_spec["width"] = int(image_spec.get("width") or _DEFAULT_IMAGE_SPEC["width"])
            image_spec["height"] = int(image_spec.get("height") or _DEFAULT_IMAGE_SPEC["height"])
        except Exception:
            image_spec["width"] = _DEFAULT_IMAGE_SPEC["width"]
            image_spec["height"] = _DEFAULT_IMAGE_SPEC["height"]
        fmt = str(image_spec.get("format") or _DEFAULT_IMAGE_SPEC["format"]).lower().strip(".")
        if fmt == "jpeg":
            fmt = "jpg"
        if fmt not in {"png", "jpg", "webp"}:
            fmt = "png"
        image_spec["format"] = fmt

        return StylePreset(
            style_prefix=style_prefix.strip(),
            constraints=constraints.strip(),
            banned_terms=banned_terms,
            default_duration_ms=max(0, default_duration_ms),
            image_spec=image_spec,
        )

