from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from factory_common import paths as fpaths

from script_pipeline.thumbnails.param_catalog_v1 import PARAM_CATALOG_V1, validate_param_value_v1


THUMB_SPEC_SCHEMA_V1 = "ytm.thumbnail.thumb_spec.v1"


@dataclass(frozen=True)
class ThumbSpecLoadResult:
    payload: Dict[str, Any]
    path: Path


def thumb_spec_path(channel: str, video: str) -> Path:
    return fpaths.thumbnail_assets_dir(channel, video) / "thumb_spec.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_allowed_override_root_key(key: str) -> bool:
    return key in {
        "bg_pan_zoom",
        "bg_enhance",
        "bg_enhance_band",
        "text_effects",
        "text_template_id",
        "text_scale",
        "text_offset_x",
        "text_offset_y",
        "text_fills",
        "overlays",
        "copy_override",
        "portrait",
    }


def _flatten_overrides(overrides: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flatten thumb_spec.overrides into Parameter Catalog leaf paths (overrides.*).
    """
    out: Dict[str, Any] = {}
    for root_key, root_value in overrides.items():
        if not isinstance(root_key, str) or not root_key:
            continue
        if not _is_allowed_override_root_key(root_key):
            raise KeyError(f"unknown overrides key: {root_key}")

        if root_key in {"text_template_id"}:
            out["overrides.text_template_id"] = root_value
            continue

        if root_key in {"text_scale"}:
            out["overrides.text_scale"] = root_value
            continue

        if root_key in {"text_offset_x", "text_offset_y"}:
            out[f"overrides.{root_key}"] = root_value
            continue

        if root_key == "text_fills":
            if not isinstance(root_value, dict):
                raise TypeError("text_fills must be an object")
            for fill_key, fill_value in root_value.items():
                if not isinstance(fill_key, str) or not fill_key.strip():
                    continue
                if not isinstance(fill_value, dict):
                    raise TypeError(f"text_fills.{fill_key} must be an object")
                for k, v in fill_value.items():
                    if k == "color":
                        out[f"overrides.text_fills.{fill_key}.color"] = v
                        continue
                    raise KeyError(f"unknown text_fills.{fill_key} key: {k}")
            continue

        if root_key == "copy_override":
            if not isinstance(root_value, dict):
                raise TypeError("copy_override must be an object")
            for k in ("upper", "title", "lower"):
                if k in root_value:
                    out[f"overrides.copy_override.{k}"] = root_value.get(k)
            for k in root_value.keys():
                if k not in {"upper", "title", "lower"}:
                    raise KeyError(f"unknown copy_override key: {k}")
            continue

        if root_key == "portrait":
            if not isinstance(root_value, dict):
                raise TypeError("portrait must be an object")
            allowed = {"zoom", "offset_x", "offset_y", "trim_transparent", "fg_brightness", "fg_contrast", "fg_color"}
            for k, v in root_value.items():
                out[f"overrides.portrait.{k}"] = v
            for k in root_value.keys():
                if k not in allowed:
                    raise KeyError(f"unknown portrait key: {k}")
            continue

        if not isinstance(root_value, dict):
            raise TypeError(f"{root_key} must be an object")

        if root_key in {"bg_pan_zoom", "bg_enhance", "bg_enhance_band"}:
            for k, v in root_value.items():
                out[f"overrides.{root_key}.{k}"] = v
            continue

        if root_key == "text_effects":
            for eff_key, eff_val in root_value.items():
                if eff_key not in {"stroke", "shadow", "glow"}:
                    raise KeyError(f"unknown text_effects key: {eff_key}")
                if not isinstance(eff_val, dict):
                    raise TypeError(f"text_effects.{eff_key} must be an object")
                for k, v in eff_val.items():
                    out[f"overrides.text_effects.{eff_key}.{k}"] = v
            continue

        if root_key == "overlays":
            for overlay_key, overlay_val in root_value.items():
                if overlay_key not in {"left_tsz", "top_band", "bottom_band"}:
                    raise KeyError(f"unknown overlays key: {overlay_key}")
                if not isinstance(overlay_val, dict):
                    raise TypeError(f"overlays.{overlay_key} must be an object")
                for k, v in overlay_val.items():
                    out[f"overrides.overlays.{overlay_key}.{k}"] = v
            continue

        raise KeyError(f"unsupported overrides key: {root_key}")
    return out


def validate_thumb_spec_payload(payload: Dict[str, Any], *, channel: str, video: str) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("thumb_spec must be a JSON object")

    schema = str(payload.get("schema") or "").strip()
    if schema != THUMB_SPEC_SCHEMA_V1:
        raise ValueError(f"unexpected schema: {schema!r}")

    ch = str(payload.get("channel") or "").strip().upper()
    vid = str(payload.get("video") or "").strip().zfill(3)
    if ch and ch != str(channel).strip().upper():
        raise ValueError(f"channel mismatch: {ch} != {channel}")
    if vid and vid != str(video).strip().zfill(3):
        raise ValueError(f"video mismatch: {vid} != {video}")

    allowed_top_keys = {"schema", "channel", "video", "overrides", "updated_at"}
    unknown_top = [k for k in payload.keys() if k not in allowed_top_keys]
    if unknown_top:
        raise KeyError(f"unknown top-level keys: {unknown_top}")

    overrides = payload.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise TypeError("overrides must be an object")

    leaf_map = _flatten_overrides(overrides)
    normalized: Dict[str, Any] = {}
    for leaf_path, raw_value in leaf_map.items():
        if leaf_path not in PARAM_CATALOG_V1:
            raise KeyError(f"override path not allowed: {leaf_path}")
        if not leaf_path.startswith("overrides."):
            raise KeyError(f"unexpected override path: {leaf_path}")
        normalized[leaf_path] = validate_param_value_v1(leaf_path, raw_value)

    out = {
        "schema": THUMB_SPEC_SCHEMA_V1,
        "channel": str(channel).strip().upper(),
        "video": str(video).strip().zfill(3),
        "overrides": overrides,
        "updated_at": str(payload.get("updated_at") or "").strip() or None,
        "_normalized_overrides_leaf": normalized,
    }
    return out


def load_thumb_spec(channel: str, video: str) -> Optional[ThumbSpecLoadResult]:
    path = thumb_spec_path(channel, video)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    validated = validate_thumb_spec_payload(payload, channel=channel, video=video)
    return ThumbSpecLoadResult(payload=validated, path=path)


def save_thumb_spec(channel: str, video: str, overrides: Dict[str, Any]) -> Path:
    path = thumb_spec_path(channel, video)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": THUMB_SPEC_SCHEMA_V1,
        "channel": str(channel).strip().upper(),
        "video": str(video).strip().zfill(3),
        "overrides": overrides,
        "updated_at": _utc_now_iso(),
    }
    validate_thumb_spec_payload(payload, channel=channel, video=video)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def extract_normalized_override_leaf(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Read normalized leaf overrides from `validate_thumb_spec_payload` result.
    """
    leaf = payload.get("_normalized_overrides_leaf")
    return leaf if isinstance(leaf, dict) else {}
