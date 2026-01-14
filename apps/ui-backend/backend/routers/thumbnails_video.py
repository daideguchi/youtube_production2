from __future__ import annotations

import io
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from PIL import Image

import backend.main as backend_main
from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.app.path_utils import safe_relative_path
from backend.app.thumbnails_specs_models import ThumbnailTextLineSpecLinePayload, ThumbnailThumbSpecUpdateRequest
from backend.app.thumbnails_video_models import (
    ThumbnailAssetReplaceResponse,
    ThumbnailVariantComposeRequest,
    ThumbnailVariantCreateRequest,
    ThumbnailVariantGenerateRequest,
    ThumbnailVariantPatchRequest,
)
from backend.app.thumbnails_variant_models import ThumbnailVariantResponse
from backend.main import (
    ThumbnailCommentPatchRequest,
    ThumbnailCommentPatchResponse,
    ThumbnailCommentPatchTargetResponse,
    ThumbnailEditorContextResponse,
    ThumbnailLayerSpecsBuildRequest,
    ThumbnailLayerSpecsBuildResponse,
    ThumbnailPreviewTextLayerResponse,
    ThumbnailPreviewTextLayerSlotsRequest,
    ThumbnailPreviewTextLayerSlotsResponse,
    ThumbnailPreviewTextSlotImageResponse,
    ThumbnailTextSlotMetaResponse,
    ThumbnailTextTemplateOptionResponse,
    ThumbnailTwoUpBuildResponse,
)
from factory_common.paths import ssot_assets_root, ssot_thumbnails_root
from script_pipeline.tools import planning_store

router = APIRouter(tags=["thumbnails"])

# Shared constants from backend.main (avoid duplication across routers).
PROJECT_ROOT = backend_main.PROJECT_ROOT
THUMBNAIL_ASSETS_DIR = backend_main.THUMBNAIL_ASSETS_DIR
THUMBNAIL_TEMPLATES_LOCK = backend_main.THUMBNAIL_TEMPLATES_LOCK
THUMBNAIL_PROJECTS_LOCK = backend_main.THUMBNAIL_PROJECTS_LOCK
THUMBNAIL_COMMENT_PATCH_SCHEMA_V1 = backend_main.THUMBNAIL_COMMENT_PATCH_SCHEMA_V1

# Late-bound helpers defined in backend.main after router wiring.
# (Keep wrappers to avoid circular-import ordering issues.)

def _normalize_thumbnail_stable_id(raw: Optional[str]) -> Optional[str]:
    return backend_main._normalize_thumbnail_stable_id(raw)


def _thumb_spec_stable_path(channel_code: str, video_number: str, stable: str) -> Path:
    return backend_main._thumb_spec_stable_path(channel_code, video_number, stable)


def _text_line_spec_stable_path(channel_code: str, video_number: str, stable: str) -> Path:
    return backend_main._text_line_spec_stable_path(channel_code, video_number, stable)


def _elements_spec_stable_path(channel_code: str, video_number: str, stable: str) -> Path:
    return backend_main._elements_spec_stable_path(channel_code, video_number, stable)


def _load_thumbnail_templates_document():
    return backend_main._load_thumbnail_templates_document()


def _load_thumbnail_projects_document():
    return backend_main._load_thumbnail_projects_document()


def _write_thumbnail_projects_document(path: Path, payload: dict) -> None:
    backend_main._write_thumbnail_projects_document(path, payload)


def _get_or_create_thumbnail_project(payload: dict, channel_code: str, video_number: str) -> dict:
    return backend_main._get_or_create_thumbnail_project(payload, channel_code, video_number)


def _normalize_thumbnail_status(status: Optional[str]) -> str:
    return backend_main._normalize_thumbnail_status(status)


def _normalize_thumbnail_tags(tags: Optional[Iterable[str]]) -> Optional[List[str]]:
    return backend_main._normalize_thumbnail_tags(tags)


def _normalize_thumbnail_image_bytes(image_bytes: bytes, *, width: int = 1280, height: int = 720) -> bytes:
    return backend_main._normalize_thumbnail_image_bytes(image_bytes, width=width, height=height)


def _sanitize_library_filename(name: str, *, default_prefix: str) -> str:
    return backend_main._sanitize_library_filename(name, default_prefix=default_prefix)


def _ensure_unique_filename(directory: Path, filename: str) -> Path:
    return backend_main._ensure_unique_filename(directory, filename)


def _persist_thumbnail_variant(*args: Any, **kwargs: Any) -> ThumbnailVariantResponse:
    return backend_main._persist_thumbnail_variant(*args, **kwargs)


def _build_thumbnail_template_context(channel_code: str, video_number: str) -> Dict[str, str]:
    return backend_main._build_thumbnail_template_context(channel_code, video_number)


def _render_thumbnail_prompt_template(template: str, context: Dict[str, str]) -> str:
    return backend_main._render_thumbnail_prompt_template(template, context)


def _fetch_openrouter_generation(gen_id: str, *, timeout_sec: int = 10) -> Optional[Dict[str, Any]]:
    return backend_main._fetch_openrouter_generation(gen_id, timeout_sec=timeout_sec)


async def _save_upload_file(upload: UploadFile, destination: Path) -> None:
    await backend_main._save_upload_file(upload, destination)
@router.get(
    "/api/workspaces/thumbnails/{channel}/{video}/editor-context",
    response_model=ThumbnailEditorContextResponse,
)
def get_thumbnail_editor_context(
    channel: str,
    video: str,
    stable: Optional[str] = Query(None, description="stable output id (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    video_id = f"{channel_code}-{video_number}"
    stable_id = _normalize_thumbnail_stable_id(stable if stable is not None else variant)

    try:
        from script_pipeline.thumbnails.compiler.layer_specs import (
            find_text_layout_item_for_video,
            load_layer_spec_yaml,
            resolve_channel_layer_spec_ids,
        )
        from script_pipeline.thumbnails.layers.image_layer import find_existing_portrait
        from script_pipeline.thumbnails.thumb_spec import extract_normalized_override_leaf, load_thumb_spec, validate_thumb_spec_payload
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"thumbnail compiler modules are not available: {exc}") from exc

    # Channel compiler defaults (templates.json)
    compiler_defaults: Dict[str, Any] = {}
    with THUMBNAIL_TEMPLATES_LOCK:
        _, doc = _load_thumbnail_templates_document()
        channels = doc.get("channels") if isinstance(doc, dict) else None
        channel_doc = channels.get(channel_code) if isinstance(channels, dict) else None
        if isinstance(channel_doc, dict) and isinstance(channel_doc.get("compiler_defaults"), dict):
            compiler_defaults = channel_doc.get("compiler_defaults") or {}

    # Layer specs context (text_layout v3)
    _, text_layout_id = resolve_channel_layer_spec_ids(channel_code)
    text_layout_spec: Dict[str, Any] = {}
    template_id_default: Optional[str] = None
    template_options: List[ThumbnailTextTemplateOptionResponse] = []
    text_slots: Dict[str, str] = {}
    if not (isinstance(text_layout_id, str) and text_layout_id.strip()):
        # Allow UI tuning even when a channel is not explicitly configured.
        text_layout_id = "text_layout_v3"
    if isinstance(text_layout_id, str) and text_layout_id.strip():
        try:
            text_layout_spec = load_layer_spec_yaml(text_layout_id.strip())
        except Exception:
            text_layout_spec = {}

    if isinstance(text_layout_spec, dict):
        item = find_text_layout_item_for_video(text_layout_spec, video_id)
        if isinstance(item, dict):
            template_id_default = str(item.get("template_id") or "").strip() or None
            payload = item.get("text") if isinstance(item.get("text"), dict) else {}
            for raw_key, raw_value in (payload or {}).items():
                if not isinstance(raw_key, str) or not raw_key.strip():
                    continue
                if raw_value is None:
                    continue
                text_slots[raw_key.strip()] = str(raw_value)
        templates_payload = text_layout_spec.get("templates")
        if isinstance(templates_payload, dict):
            for tpl_id, tpl in sorted(templates_payload.items(), key=lambda kv: str(kv[0])):
                if not isinstance(tpl_id, str) or not tpl_id.strip():
                    continue
                desc = None
                if isinstance(tpl, dict) and isinstance(tpl.get("description"), str):
                    desc = str(tpl.get("description") or "").strip() or None
                slots_meta: Dict[str, ThumbnailTextSlotMetaResponse] = {}
                slots_payload = tpl.get("slots") if isinstance(tpl, dict) else None
                if isinstance(slots_payload, dict):
                    for slot_id, slot_cfg in slots_payload.items():
                        if not isinstance(slot_id, str) or not slot_id.strip():
                            continue
                        if not isinstance(slot_cfg, dict):
                            continue
                        box_payload = slot_cfg.get("box")
                        box: Optional[List[float]] = None
                        if isinstance(box_payload, (list, tuple)) and len(box_payload) == 4:
                            try:
                                box = [
                                    float(box_payload[0]),
                                    float(box_payload[1]),
                                    float(box_payload[2]),
                                    float(box_payload[3]),
                                ]
                            except Exception:
                                box = None
                        fill = str(slot_cfg.get("fill") or "").strip() or None
                        base_size_px: Optional[int] = None
                        base_size_payload = slot_cfg.get("base_size_px")
                        if isinstance(base_size_payload, (int, float)) and float(base_size_payload) > 0:
                            base_size_px = int(base_size_payload)
                        align = str(slot_cfg.get("align") or "").strip() or None
                        valign = str(slot_cfg.get("valign") or "").strip() or None
                        slots_meta[slot_id.strip()] = ThumbnailTextSlotMetaResponse(
                            box=box,
                            fill=fill,
                            base_size_px=base_size_px,
                            align=align,
                            valign=valign,
                        )
                template_options.append(
                    ThumbnailTextTemplateOptionResponse(
                        id=tpl_id.strip(),
                        description=desc,
                        slots=slots_meta,
                    )
                )
    if not template_id_default and template_options:
        template_id_default = template_options[0].id

    # Existing per-video thumb_spec overrides (normalized leaf paths)
    overrides_source: Optional[Dict[str, Any]] = None
    if stable_id:
        stable_path = _thumb_spec_stable_path(channel_code, video_number, stable_id)
        if stable_path.exists():
            try:
                raw = json.loads(stable_path.read_text(encoding="utf-8"))
                overrides_source = validate_thumb_spec_payload(raw, channel=channel_code, video=video_number)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"failed to load thumb_spec.{stable_id}: {exc}") from exc
    if overrides_source is None:
        # Stable variants must not inherit thumb_spec.json implicitly.
        # Only the primary stable (00_thumb_1) may fall back to legacy thumb_spec.json.
        if stable_id and stable_id != "00_thumb_1":
            overrides_source = {}
        else:
            loaded_spec = load_thumb_spec(channel_code, video_number)
            overrides_source = loaded_spec.payload if loaded_spec else {}
    overrides_leaf_raw = extract_normalized_override_leaf(overrides_source) if overrides_source else {}

    overrides_leaf: Dict[str, Any] = {}
    for k, v in (overrides_leaf_raw or {}).items():
        if isinstance(v, tuple):
            overrides_leaf[str(k)] = list(v)
        else:
            overrides_leaf[str(k)] = v

    # Defaults (as leaf paths from PARAM_CATALOG_V1)
    defaults_leaf: Dict[str, Any] = {}

    bg_defaults = compiler_defaults.get("bg_enhance") if isinstance(compiler_defaults.get("bg_enhance"), dict) else {}
    pan_defaults = compiler_defaults.get("bg_pan_zoom") if isinstance(compiler_defaults.get("bg_pan_zoom"), dict) else {}
    band_defaults = compiler_defaults.get("bg_enhance_band") if isinstance(compiler_defaults.get("bg_enhance_band"), dict) else {}

    defaults_leaf["overrides.bg_enhance.brightness"] = float(bg_defaults.get("brightness", 1.0))
    defaults_leaf["overrides.bg_enhance.contrast"] = float(bg_defaults.get("contrast", 1.0))
    defaults_leaf["overrides.bg_enhance.color"] = float(bg_defaults.get("color", 1.0))
    defaults_leaf["overrides.bg_enhance.gamma"] = float(bg_defaults.get("gamma", 1.0))

    defaults_leaf["overrides.bg_pan_zoom.zoom"] = float(pan_defaults.get("zoom", 1.0))
    defaults_leaf["overrides.bg_pan_zoom.pan_x"] = float(pan_defaults.get("pan_x", 0.0))
    defaults_leaf["overrides.bg_pan_zoom.pan_y"] = float(pan_defaults.get("pan_y", 0.0))

    defaults_leaf["overrides.bg_enhance_band.x0"] = float(band_defaults.get("x0", 0.0))
    defaults_leaf["overrides.bg_enhance_band.x1"] = float(band_defaults.get("x1", 0.0))
    defaults_leaf["overrides.bg_enhance_band.power"] = float(band_defaults.get("power", 1.0))
    defaults_leaf["overrides.bg_enhance_band.brightness"] = float(band_defaults.get("brightness", 1.0))
    defaults_leaf["overrides.bg_enhance_band.contrast"] = float(band_defaults.get("contrast", 1.0))
    defaults_leaf["overrides.bg_enhance_band.color"] = float(band_defaults.get("color", 1.0))
    defaults_leaf["overrides.bg_enhance_band.gamma"] = float(band_defaults.get("gamma", 1.0))

    if template_id_default:
        defaults_leaf["overrides.text_template_id"] = template_id_default
    defaults_leaf["overrides.text_scale"] = 1.0

    global_cfg = text_layout_spec.get("global") if isinstance(text_layout_spec, dict) else None
    global_cfg = global_cfg if isinstance(global_cfg, dict) else {}
    effects_defaults = global_cfg.get("effects_defaults") if isinstance(global_cfg.get("effects_defaults"), dict) else {}

    stroke_cfg = effects_defaults.get("stroke") if isinstance(effects_defaults.get("stroke"), dict) else {}
    shadow_cfg = effects_defaults.get("shadow") if isinstance(effects_defaults.get("shadow"), dict) else {}
    glow_cfg = effects_defaults.get("glow") if isinstance(effects_defaults.get("glow"), dict) else {}

    defaults_leaf["overrides.text_effects.stroke.width_px"] = int(stroke_cfg.get("width_px", 8))
    defaults_leaf["overrides.text_effects.stroke.color"] = str(stroke_cfg.get("color") or "#000000")

    defaults_leaf["overrides.text_effects.shadow.alpha"] = float(shadow_cfg.get("alpha", 0.65))
    shadow_off = shadow_cfg.get("offset_px") or [6, 6]
    try:
        defaults_leaf["overrides.text_effects.shadow.offset_px"] = [int(shadow_off[0]), int(shadow_off[1])]
    except Exception:
        defaults_leaf["overrides.text_effects.shadow.offset_px"] = [6, 6]
    defaults_leaf["overrides.text_effects.shadow.blur_px"] = int(shadow_cfg.get("blur_px", 10))
    defaults_leaf["overrides.text_effects.shadow.color"] = str(shadow_cfg.get("color") or "#000000")

    defaults_leaf["overrides.text_effects.glow.alpha"] = float(glow_cfg.get("alpha", 0.0))
    defaults_leaf["overrides.text_effects.glow.blur_px"] = int(glow_cfg.get("blur_px", 0))
    defaults_leaf["overrides.text_effects.glow.color"] = str(glow_cfg.get("color") or "#ffffff")

    for fill_key in ("white_fill", "red_fill", "yellow_fill", "hot_red_fill", "purple_fill"):
        fill_cfg = effects_defaults.get(fill_key) if isinstance(effects_defaults.get(fill_key), dict) else None
        if not isinstance(fill_cfg, dict):
            continue
        if str(fill_cfg.get("mode") or "").strip().lower() != "solid":
            continue
        color = str(fill_cfg.get("color") or "").strip()
        if color:
            defaults_leaf[f"overrides.text_fills.{fill_key}.color"] = color

    overlays_cfg = global_cfg.get("overlays") if isinstance(global_cfg.get("overlays"), dict) else {}
    left_tsz = overlays_cfg.get("left_tsz") if isinstance(overlays_cfg.get("left_tsz"), dict) else None
    if isinstance(left_tsz, dict):
        defaults_leaf["overrides.overlays.left_tsz.enabled"] = bool(left_tsz.get("enabled", True))
        defaults_leaf["overrides.overlays.left_tsz.color"] = str(left_tsz.get("color") or "#000000")
        defaults_leaf["overrides.overlays.left_tsz.alpha_left"] = float(left_tsz.get("alpha_left", 0.65))
        defaults_leaf["overrides.overlays.left_tsz.alpha_right"] = float(left_tsz.get("alpha_right", 0.0))
        defaults_leaf["overrides.overlays.left_tsz.x0"] = float(left_tsz.get("x0", 0.0))
        defaults_leaf["overrides.overlays.left_tsz.x1"] = float(left_tsz.get("x1", 0.52))

    top_band = overlays_cfg.get("top_band") if isinstance(overlays_cfg.get("top_band"), dict) else None
    if isinstance(top_band, dict):
        defaults_leaf["overrides.overlays.top_band.enabled"] = bool(top_band.get("enabled", True))
        defaults_leaf["overrides.overlays.top_band.color"] = str(top_band.get("color") or "#000000")
        defaults_leaf["overrides.overlays.top_band.alpha_top"] = float(top_band.get("alpha_top", 0.70))
        defaults_leaf["overrides.overlays.top_band.alpha_bottom"] = float(top_band.get("alpha_bottom", 0.0))
        defaults_leaf["overrides.overlays.top_band.y0"] = float(top_band.get("y0", 0.0))
        defaults_leaf["overrides.overlays.top_band.y1"] = float(top_band.get("y1", 0.25))

    bottom_band = overlays_cfg.get("bottom_band") if isinstance(overlays_cfg.get("bottom_band"), dict) else None
    if isinstance(bottom_band, dict):
        defaults_leaf["overrides.overlays.bottom_band.enabled"] = bool(bottom_band.get("enabled", True))
        defaults_leaf["overrides.overlays.bottom_band.color"] = str(bottom_band.get("color") or "#000000")
        defaults_leaf["overrides.overlays.bottom_band.alpha_top"] = float(bottom_band.get("alpha_top", 0.0))
        defaults_leaf["overrides.overlays.bottom_band.alpha_bottom"] = float(bottom_band.get("alpha_bottom", 0.80))
        defaults_leaf["overrides.overlays.bottom_band.y0"] = float(bottom_band.get("y0", 0.70))
        defaults_leaf["overrides.overlays.bottom_band.y1"] = float(bottom_band.get("y1", 1.0))

    # Portrait defaults (CH26 policy + generic fallbacks)
    portrait_dest_box_norm: List[float] = [0.29, 0.06, 0.42, 0.76]
    portrait_anchor: str = "bottom_center"
    video_dir = THUMBNAIL_ASSETS_DIR / channel_code / video_number
    portrait_available = bool(find_existing_portrait(video_dir))
    defaults_leaf["overrides.portrait.enabled"] = stable_id != "00_thumb_2"
    defaults_leaf["overrides.portrait.suppress_bg"] = channel_code == "CH26" and stable_id != "00_thumb_2"
    defaults_leaf["overrides.portrait.zoom"] = 1.0
    defaults_leaf["overrides.portrait.offset_x"] = 0.0
    defaults_leaf["overrides.portrait.offset_y"] = 0.0
    defaults_leaf["overrides.portrait.trim_transparent"] = False
    defaults_leaf["overrides.portrait.fg_brightness"] = 1.20
    defaults_leaf["overrides.portrait.fg_contrast"] = 1.08
    defaults_leaf["overrides.portrait.fg_color"] = 0.98

    if channel_code == "CH26":
        policy_path = ssot_thumbnails_root() / "compiler" / "policies" / "ch26_portrait_overrides_v1.yaml"
        try:
            policy_payload = yaml.safe_load(policy_path.read_text(encoding="utf-8")) if policy_path.exists() else {}
        except Exception:
            policy_payload = {}
        defaults_payload = policy_payload.get("defaults") if isinstance(policy_payload, dict) else None
        defaults_payload = defaults_payload if isinstance(defaults_payload, dict) else {}
        ov_payload = policy_payload.get("overrides") if isinstance(policy_payload, dict) else None
        ov_payload = ov_payload if isinstance(ov_payload, dict) else {}
        video_ov = ov_payload.get(video_number) if isinstance(ov_payload.get(video_number), dict) else {}

        dest_box = video_ov.get("dest_box", defaults_payload.get("dest_box"))
        if isinstance(dest_box, (list, tuple)) and len(dest_box) == 4:
            try:
                portrait_dest_box_norm = [float(dest_box[0]), float(dest_box[1]), float(dest_box[2]), float(dest_box[3])]
            except Exception:
                portrait_dest_box_norm = portrait_dest_box_norm

        anchor = video_ov.get("anchor", defaults_payload.get("anchor"))
        if isinstance(anchor, str) and anchor.strip():
            portrait_anchor = anchor.strip()

        defaults_leaf["overrides.portrait.zoom"] = float(video_ov.get("zoom", defaults_payload.get("zoom", 1.0)))
        off = video_ov.get("offset", defaults_payload.get("offset", [0.0, 0.0]))
        try:
            defaults_leaf["overrides.portrait.offset_x"] = float(off[0])
            defaults_leaf["overrides.portrait.offset_y"] = float(off[1])
        except Exception:
            defaults_leaf["overrides.portrait.offset_x"] = 0.0
            defaults_leaf["overrides.portrait.offset_y"] = 0.0
        defaults_leaf["overrides.portrait.trim_transparent"] = bool(
            video_ov.get("trim_transparent", defaults_payload.get("trim_transparent", False))
        )
        fg_defaults = defaults_payload.get("fg") if isinstance(defaults_payload.get("fg"), dict) else {}
        fg_ov = video_ov.get("fg") if isinstance(video_ov.get("fg"), dict) else {}
        defaults_leaf["overrides.portrait.fg_brightness"] = float(fg_ov.get("brightness", fg_defaults.get("brightness", 1.20)))
        defaults_leaf["overrides.portrait.fg_contrast"] = float(fg_ov.get("contrast", fg_defaults.get("contrast", 1.08)))
        defaults_leaf["overrides.portrait.fg_color"] = float(fg_ov.get("color", fg_defaults.get("color", 0.98)))

    effective_leaf = dict(defaults_leaf)
    effective_leaf.update(overrides_leaf)

    return ThumbnailEditorContextResponse(
        channel=channel_code,
        video=video_number,
        video_id=video_id,
        portrait_available=portrait_available,
        portrait_dest_box_norm=portrait_dest_box_norm,
        portrait_anchor=portrait_anchor,
        template_id_default=template_id_default,
        template_options=template_options,
        text_slots=text_slots,
        defaults_leaf=defaults_leaf,
        overrides_leaf=overrides_leaf,
        effective_leaf=effective_leaf,
    )


@router.post(
    "/api/workspaces/thumbnails/{channel}/{video}/preview/text-layer",
    response_model=ThumbnailPreviewTextLayerResponse,
)
def preview_thumbnail_text_layer(
    channel: str,
    video: str,
    request: ThumbnailThumbSpecUpdateRequest,
    stable: Optional[str] = Query(None, description="optional stable id to namespace output (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    """
    Render a transparent text layer PNG using the same compositor as the real build.

    Notes:
    - No LLM is used.
    - Overlays (left_tsz/top/bottom bands) are disabled here so the UI can render them as a separate fixed layer.
    - `overrides.text_offset_*` is intentionally ignored and applied as a client-side translate for smooth dragging.
    """
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    video_id = f"{channel_code}-{video_number}"
    stable_id = _normalize_thumbnail_stable_id(stable if stable is not None else variant)

    overrides_leaf = request.overrides if isinstance(request.overrides, dict) else {}
    overrides_leaf = {str(k): v for k, v in overrides_leaf.items() if isinstance(k, str)}

    try:
        import copy
        from PIL import Image

        from script_pipeline.thumbnails.compiler.layer_specs import (
            find_text_layout_item_for_video,
            load_layer_spec_yaml,
            resolve_channel_layer_spec_ids,
        )
        from script_pipeline.thumbnails.layers.text_layer import compose_text_to_png
        from script_pipeline.thumbnails.tools.layer_specs_builder import _load_planning_copy, _planning_value_for_slot
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"thumbnail text preview dependencies are not available: {exc}") from exc

    try:
        _, text_layout_id = resolve_channel_layer_spec_ids(channel_code)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to resolve text_layout spec id: {exc}") from exc
    if not (isinstance(text_layout_id, str) and str(text_layout_id).strip()):
        text_layout_id = "text_layout_v3"

    try:
        text_layout_spec = load_layer_spec_yaml(str(text_layout_id).strip())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to load text_layout spec: {exc}") from exc

    try:
        item = find_text_layout_item_for_video(text_layout_spec, video_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to resolve video item in text_layout spec: {exc}") from exc

    templates_payload = text_layout_spec.get("templates") if isinstance(text_layout_spec, dict) else None
    if not isinstance(templates_payload, dict):
        raise HTTPException(status_code=500, detail="text_layout.templates is missing")

    template_id = str(item.get("template_id") or "").strip() if isinstance(item, dict) else ""
    template_id_override = str(overrides_leaf.get("overrides.text_template_id") or "").strip() or None
    effective_template_id = str(template_id_override or template_id).strip()
    if not effective_template_id:
        candidates = [str(k).strip() for k in templates_payload.keys() if str(k).strip()]
        candidates.sort()
        effective_template_id = candidates[0] if candidates else ""
    if not effective_template_id:
        raise HTTPException(status_code=500, detail="template_id is missing")
    tpl_payload = templates_payload.get(effective_template_id)
    if not isinstance(tpl_payload, dict):
        raise HTTPException(status_code=500, detail=f"template_id not found: {effective_template_id}")
    slots_payload = tpl_payload.get("slots")
    if not isinstance(slots_payload, dict):
        raise HTTPException(status_code=500, detail=f"template slots missing for {effective_template_id}")

    # Apply text_scale by mutating base_size_px in a deep-copied spec (matches build pipeline behavior).
    text_scale_raw = overrides_leaf.get("overrides.text_scale", 1.0)
    try:
        text_scale = float(text_scale_raw)
    except Exception:
        text_scale = 1.0
    if abs(float(text_scale) - 1.0) > 1e-6:
        text_layout_spec = copy.deepcopy(text_layout_spec)
        templates_payload = text_layout_spec.get("templates") if isinstance(text_layout_spec, dict) else None
        tpl_payload = templates_payload.get(effective_template_id) if isinstance(templates_payload, dict) else None
        slots_payload = tpl_payload.get("slots") if isinstance(tpl_payload, dict) else None
        if isinstance(slots_payload, dict):
            for slot_cfg in slots_payload.values():
                if not isinstance(slot_cfg, dict):
                    continue
                base_size = slot_cfg.get("base_size_px")
                if isinstance(base_size, (int, float)) and float(base_size) > 0:
                    scaled = int(round(float(base_size) * float(text_scale)))
                    slot_cfg["base_size_px"] = max(1, scaled)

    # If the video is not present in the spec, synthesize a minimal item so the compositor can run.
    if not isinstance(item, dict):
        if not isinstance(text_layout_spec, dict):
            raise HTTPException(status_code=500, detail="text_layout spec is invalid")
        text_layout_spec = copy.deepcopy(text_layout_spec)
        items_payload = text_layout_spec.get("items")
        if not isinstance(items_payload, list):
            items_payload = []
            text_layout_spec["items"] = items_payload
        slot_keys = [str(k).strip() for k in slots_payload.keys() if isinstance(k, str) and str(k).strip()]
        if not slot_keys:
            slot_keys = ["main"]
        item = {
            "video_id": video_id,
            "title": video_id,
            "template_id": effective_template_id,
            "text": {k: "" for k in slot_keys},
        }
        items_payload.append(item)

    # Build text_override: slot-specific manual overrides win, then fall back to authored text, then planning copy.
    text_payload = item.get("text") if isinstance(item, dict) and isinstance(item.get("text"), dict) else {}
    planning_copy = _load_planning_copy(channel_code, video_number)

    copy_upper = str(overrides_leaf.get("overrides.copy_override.upper") or "").strip()
    copy_title = str(overrides_leaf.get("overrides.copy_override.title") or "").strip()
    copy_lower = str(overrides_leaf.get("overrides.copy_override.lower") or "").strip()

    def _override_for_slot(slot_name: str) -> str:
        key = str(slot_name or "").strip().lower()
        if key in {"line1", "upper", "top"}:
            return copy_upper
        if key in {"line2", "title", "main"}:
            return copy_title
        if key in {"line3", "lower", "accent"}:
            return copy_lower
        return ""

    text_override: Dict[str, str] = {}
    for slot_name in slots_payload.keys():
        slot_key = str(slot_name or "").strip()
        if not slot_key:
            continue

        forced = _override_for_slot(slot_key)
        if forced:
            text_override[slot_key] = forced
            continue

        authored = str(text_payload.get(slot_key) or "").strip()
        if authored:
            continue

        planned = _planning_value_for_slot(slot_key, planning_copy)
        if planned:
            text_override[slot_key] = planned

    # Build effects_override (allowlist) from leaf overrides.
    effects_override: Dict[str, Any] = {}
    stroke: Dict[str, Any] = {}
    shadow: Dict[str, Any] = {}
    glow: Dict[str, Any] = {}
    fills: Dict[str, Any] = {}

    if "overrides.text_effects.stroke.width_px" in overrides_leaf:
        stroke["width_px"] = overrides_leaf["overrides.text_effects.stroke.width_px"]
    if "overrides.text_effects.stroke.color" in overrides_leaf:
        stroke["color"] = overrides_leaf["overrides.text_effects.stroke.color"]
    if "overrides.text_effects.shadow.alpha" in overrides_leaf:
        shadow["alpha"] = overrides_leaf["overrides.text_effects.shadow.alpha"]
    if "overrides.text_effects.shadow.offset_px" in overrides_leaf:
        shadow["offset_px"] = overrides_leaf["overrides.text_effects.shadow.offset_px"]
    if "overrides.text_effects.shadow.blur_px" in overrides_leaf:
        shadow["blur_px"] = overrides_leaf["overrides.text_effects.shadow.blur_px"]
    if "overrides.text_effects.shadow.color" in overrides_leaf:
        shadow["color"] = overrides_leaf["overrides.text_effects.shadow.color"]
    if "overrides.text_effects.glow.alpha" in overrides_leaf:
        glow["alpha"] = overrides_leaf["overrides.text_effects.glow.alpha"]
    if "overrides.text_effects.glow.blur_px" in overrides_leaf:
        glow["blur_px"] = overrides_leaf["overrides.text_effects.glow.blur_px"]
    if "overrides.text_effects.glow.color" in overrides_leaf:
        glow["color"] = overrides_leaf["overrides.text_effects.glow.color"]

    for fill_key in ("white_fill", "red_fill", "yellow_fill", "hot_red_fill", "purple_fill"):
        path = f"overrides.text_fills.{fill_key}.color"
        if path in overrides_leaf:
            fills[fill_key] = {"color": overrides_leaf[path]}

    if stroke:
        effects_override["stroke"] = stroke
    if shadow:
        effects_override["shadow"] = shadow
    if glow:
        effects_override["glow"] = glow
    if fills:
        effects_override.update(fills)

    # Overlays are rendered separately in the UI, so disable them in this "text-only" render.
    overlays_override = {
        "left_tsz": {"enabled": False},
        "top_band": {"enabled": False},
        "bottom_band": {"enabled": False},
    }

    # Prepare output paths under the canonical workspace assets tree.
    preview_dir = THUMBNAIL_ASSETS_DIR / channel_code / video_number / "compiler" / "ui_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    base_path = preview_dir / "base_transparent.png"
    out_name = f"text_layer__{stable_id}.png" if stable_id else "text_layer.png"
    out_path = preview_dir / out_name

    # Ensure we have a transparent base image at the correct resolution.
    canvas = text_layout_spec.get("canvas") if isinstance(text_layout_spec, dict) else None
    try:
        w = int(canvas.get("w", 1920)) if isinstance(canvas, dict) else 1920
        h = int(canvas.get("h", 1080)) if isinstance(canvas, dict) else 1080
    except Exception:
        w, h = (1920, 1080)
    w = max(1, w)
    h = max(1, h)
    if not base_path.exists():
        Image.new("RGBA", (w, h), (0, 0, 0, 0)).save(base_path, format="PNG")

    try:
        compose_text_to_png(
            base_path,
            text_layout_spec=text_layout_spec,
            video_id=video_id,
            out_path=out_path,
            output_mode="draft",
            text_override=text_override if text_override else None,
            template_id_override=template_id_override,
            effects_override=effects_override if effects_override else None,
            overlays_override=overlays_override,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to render text layer: {exc}") from exc

    rel = safe_relative_path(out_path) or str(out_path)
    url = f"/thumbnails/assets/{channel_code}/{video_number}/compiler/ui_preview/{out_name}"
    return ThumbnailPreviewTextLayerResponse(
        status="ok",
        channel=channel_code,
        video=video_number,
        image_url=url,
        image_path=rel,
    )


@router.post(
    "/api/workspaces/thumbnails/{channel}/{video}/preview/text-layer/slots",
    response_model=ThumbnailPreviewTextLayerSlotsResponse,
)
def preview_thumbnail_text_layer_slots(
    channel: str,
    video: str,
    request: ThumbnailPreviewTextLayerSlotsRequest,
    stable: Optional[str] = Query(None, description="optional stable id to namespace output (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    """
    Render per-slot transparent text layer PNGs so the UI can treat each line like Canva.

    Notes:
    - No LLM is used.
    - Overlays (left_tsz/top/bottom bands) are disabled here so the UI can render them as a separate fixed layer.
    - `overrides.text_offset_*` is intentionally ignored and applied as a client-side translate for smooth dragging.
    """
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    video_id = f"{channel_code}-{video_number}"
    stable_id = _normalize_thumbnail_stable_id(stable if stable is not None else variant)

    overrides_leaf = request.overrides if isinstance(request.overrides, dict) else {}
    overrides_leaf = {str(k): v for k, v in overrides_leaf.items() if isinstance(k, str)}
    text_line_spec_lines = request.lines if isinstance(request.lines, dict) else {}

    try:
        import copy
        from PIL import Image

        from script_pipeline.thumbnails.compiler.layer_specs import (
            find_text_layout_item_for_video,
            load_layer_spec_yaml,
            resolve_channel_layer_spec_ids,
        )
        from script_pipeline.thumbnails.layers.text_layer import compose_text_to_png
        from script_pipeline.thumbnails.tools.layer_specs_builder import _load_planning_copy, _planning_value_for_slot
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"thumbnail text preview dependencies are not available: {exc}") from exc

    try:
        _, text_layout_id = resolve_channel_layer_spec_ids(channel_code)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to resolve text_layout spec id: {exc}") from exc
    if not (isinstance(text_layout_id, str) and str(text_layout_id).strip()):
        text_layout_id = "text_layout_v3"

    try:
        text_layout_spec = load_layer_spec_yaml(str(text_layout_id).strip())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to load text_layout spec: {exc}") from exc

    try:
        item = find_text_layout_item_for_video(text_layout_spec, video_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to resolve video item in text_layout spec: {exc}") from exc

    templates_payload = text_layout_spec.get("templates") if isinstance(text_layout_spec, dict) else None
    if not isinstance(templates_payload, dict):
        raise HTTPException(status_code=500, detail="text_layout.templates is missing")

    template_id = str(item.get("template_id") or "").strip() if isinstance(item, dict) else ""
    template_id_override = str(overrides_leaf.get("overrides.text_template_id") or "").strip() or None
    effective_template_id = str(template_id_override or template_id).strip()
    if not effective_template_id:
        candidates = [str(k).strip() for k in templates_payload.keys() if str(k).strip()]
        candidates.sort()
        effective_template_id = candidates[0] if candidates else ""
    if not effective_template_id:
        raise HTTPException(status_code=500, detail="template_id is missing")
    tpl_payload = templates_payload.get(effective_template_id)
    if not isinstance(tpl_payload, dict):
        raise HTTPException(status_code=500, detail=f"template_id not found: {effective_template_id}")
    slots_payload = tpl_payload.get("slots")
    if not isinstance(slots_payload, dict):
        raise HTTPException(status_code=500, detail=f"template slots missing for {effective_template_id}")

    slot_keys = [str(k).strip() for k in slots_payload.keys() if isinstance(k, str) and str(k).strip()]

    # Apply text_scale by mutating base_size_px in a deep-copied spec (matches build pipeline behavior).
    text_scale_raw = overrides_leaf.get("overrides.text_scale", 1.0)
    try:
        text_scale = float(text_scale_raw)
    except Exception:
        text_scale = 1.0
    if abs(float(text_scale) - 1.0) > 1e-6:
        text_layout_spec = copy.deepcopy(text_layout_spec)
        templates_payload = text_layout_spec.get("templates") if isinstance(text_layout_spec, dict) else None
        tpl_payload = templates_payload.get(effective_template_id) if isinstance(templates_payload, dict) else None
        slots_payload = tpl_payload.get("slots") if isinstance(tpl_payload, dict) else None
        if isinstance(slots_payload, dict):
            for slot_cfg in slots_payload.values():
                if not isinstance(slot_cfg, dict):
                    continue
                base_size = slot_cfg.get("base_size_px")
                if isinstance(base_size, (int, float)) and float(base_size) > 0:
                    scaled = int(round(float(base_size) * float(text_scale)))
                    slot_cfg["base_size_px"] = max(1, scaled)

    # If the video is not present in the spec, synthesize a minimal item so the compositor can run.
    if not isinstance(item, dict):
        if not isinstance(text_layout_spec, dict):
            raise HTTPException(status_code=500, detail="text_layout spec is invalid")
        text_layout_spec = copy.deepcopy(text_layout_spec)
        items_payload = text_layout_spec.get("items")
        if not isinstance(items_payload, list):
            items_payload = []
            text_layout_spec["items"] = items_payload
        slot_keys_for_item = [str(k).strip() for k in slots_payload.keys() if isinstance(k, str) and str(k).strip()]
        if not slot_keys_for_item:
            slot_keys_for_item = ["main"]
        item = {
            "video_id": video_id,
            "title": video_id,
            "template_id": effective_template_id,
            "text": {k: "" for k in slot_keys_for_item},
        }
        items_payload.append(item)

    # Resolve text for each slot.
    text_payload = item.get("text") if isinstance(item, dict) and isinstance(item.get("text"), dict) else {}
    planning_copy = _load_planning_copy(channel_code, video_number)

    copy_upper = str(overrides_leaf.get("overrides.copy_override.upper") or "").strip()
    copy_title = str(overrides_leaf.get("overrides.copy_override.title") or "").strip()
    copy_lower = str(overrides_leaf.get("overrides.copy_override.lower") or "").strip()

    def _override_for_slot(slot_name: str) -> str:
        key = str(slot_name or "").strip().lower()
        if key in {"line1", "upper", "top"}:
            return copy_upper
        if key in {"line2", "title", "main"}:
            return copy_title
        if key in {"line3", "lower", "accent"}:
            return copy_lower
        return ""

    resolved_by_slot: Dict[str, str] = {}
    for slot_key in slot_keys:
        forced = _override_for_slot(slot_key)
        authored = str(text_payload.get(slot_key) or "").strip()
        planned = _planning_value_for_slot(slot_key, planning_copy)
        resolved_by_slot[slot_key] = forced or authored or planned or ""

    # Build effects_override (allowlist) from leaf overrides.
    effects_override: Dict[str, Any] = {}
    stroke: Dict[str, Any] = {}
    shadow: Dict[str, Any] = {}
    glow: Dict[str, Any] = {}
    fills: Dict[str, Any] = {}

    if "overrides.text_effects.stroke.width_px" in overrides_leaf:
        stroke["width_px"] = overrides_leaf["overrides.text_effects.stroke.width_px"]
    if "overrides.text_effects.stroke.color" in overrides_leaf:
        stroke["color"] = overrides_leaf["overrides.text_effects.stroke.color"]
    if "overrides.text_effects.shadow.alpha" in overrides_leaf:
        shadow["alpha"] = overrides_leaf["overrides.text_effects.shadow.alpha"]
    if "overrides.text_effects.shadow.offset_px" in overrides_leaf:
        shadow["offset_px"] = overrides_leaf["overrides.text_effects.shadow.offset_px"]
    if "overrides.text_effects.shadow.blur_px" in overrides_leaf:
        shadow["blur_px"] = overrides_leaf["overrides.text_effects.shadow.blur_px"]
    if "overrides.text_effects.shadow.color" in overrides_leaf:
        shadow["color"] = overrides_leaf["overrides.text_effects.shadow.color"]
    if "overrides.text_effects.glow.alpha" in overrides_leaf:
        glow["alpha"] = overrides_leaf["overrides.text_effects.glow.alpha"]
    if "overrides.text_effects.glow.blur_px" in overrides_leaf:
        glow["blur_px"] = overrides_leaf["overrides.text_effects.glow.blur_px"]
    if "overrides.text_effects.glow.color" in overrides_leaf:
        glow["color"] = overrides_leaf["overrides.text_effects.glow.color"]

    for fill_key in ("white_fill", "red_fill", "yellow_fill", "hot_red_fill", "purple_fill"):
        path = f"overrides.text_fills.{fill_key}.color"
        if path in overrides_leaf:
            fills[fill_key] = {"color": overrides_leaf[path]}

    if stroke:
        effects_override["stroke"] = stroke
    if shadow:
        effects_override["shadow"] = shadow
    if glow:
        effects_override["glow"] = glow
    if fills:
        effects_override.update(fills)

    # Overlays are rendered separately in the UI, so disable them in this "text-only" render.
    overlays_override = {
        "left_tsz": {"enabled": False},
        "top_band": {"enabled": False},
        "bottom_band": {"enabled": False},
    }

    # Prepare output paths under the canonical workspace assets tree.
    preview_dir = THUMBNAIL_ASSETS_DIR / channel_code / video_number / "compiler" / "ui_preview"
    if stable_id:
        preview_dir = preview_dir / f"text_slots__{stable_id}"
    else:
        preview_dir = preview_dir / "text_slots"
    preview_dir.mkdir(parents=True, exist_ok=True)
    base_path = preview_dir / "base_transparent.png"

    # Ensure we have a transparent base image at the correct resolution.
    canvas = text_layout_spec.get("canvas") if isinstance(text_layout_spec, dict) else None
    try:
        w = int(canvas.get("w", 1920)) if isinstance(canvas, dict) else 1920
        h = int(canvas.get("h", 1080)) if isinstance(canvas, dict) else 1080
    except Exception:
        w, h = (1920, 1080)
    w = max(1, w)
    h = max(1, h)
    if not base_path.exists():
        Image.new("RGBA", (w, h), (0, 0, 0, 0)).save(base_path, format="PNG")

    images: Dict[str, ThumbnailPreviewTextSlotImageResponse] = {}

    blank_all: Dict[str, str] = {k: "" for k in slot_keys}
    for slot_key in slot_keys:
        resolved = resolved_by_slot.get(slot_key) or ""
        if not resolved.strip():
            continue
        line_scale = 1.0
        line = text_line_spec_lines.get(slot_key)
        if isinstance(line, ThumbnailTextLineSpecLinePayload):
            line_scale = float(line.scale)
        elif isinstance(line, dict):
            try:
                line_scale = float(line.get("scale", 1.0))
            except Exception:
                line_scale = 1.0
        line_scale = max(0.25, min(4.0, float(line_scale)))

        slot_text_spec = text_layout_spec
        if abs(float(line_scale) - 1.0) > 1e-6:
            # Apply per-line scale by mutating base_size_px for this slot only.
            slot_text_spec = copy.deepcopy(text_layout_spec)
            templates_out = slot_text_spec.get("templates") if isinstance(slot_text_spec, dict) else None
            tpl_out = templates_out.get(effective_template_id) if isinstance(templates_out, dict) else None
            slots_out = tpl_out.get("slots") if isinstance(tpl_out, dict) else None
            cfg_out = slots_out.get(slot_key) if isinstance(slots_out, dict) else None
            if isinstance(cfg_out, dict):
                base_size = cfg_out.get("base_size_px")
                if isinstance(base_size, (int, float)) and float(base_size) > 0:
                    scaled = int(round(float(base_size) * float(line_scale)))
                    cfg_out["base_size_px"] = max(1, scaled)
        safe_slot = re.sub(r"[^\w.-]", "_", slot_key) or "slot"
        out_path = preview_dir / f"{safe_slot}.png"
        slot_override = dict(blank_all)
        slot_override[slot_key] = resolved
        try:
            compose_text_to_png(
                base_path,
                text_layout_spec=slot_text_spec,
                video_id=video_id,
                out_path=out_path,
                output_mode="draft",
                text_override=slot_override,
                template_id_override=template_id_override,
                effects_override=effects_override if effects_override else None,
                overlays_override=overlays_override,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to render text slot {slot_key}: {exc}") from exc
        rel = safe_relative_path(out_path) or str(out_path)
        url = f"/thumbnails/assets/{channel_code}/{video_number}/compiler/ui_preview/{preview_dir.name}/{out_path.name}"
        images[slot_key] = ThumbnailPreviewTextSlotImageResponse(image_url=url, image_path=rel)

    return ThumbnailPreviewTextLayerSlotsResponse(
        status="ok",
        channel=channel_code,
        video=video_number,
        template_id=effective_template_id,
        images=images,
    )


def _extract_thumbnail_human_comment(raw: str) -> str:
    text = str(raw or "")
    if not text.strip():
        return ""
    # Notes may have operational suffix like: "修正済み: engine=...". Strip it.
    if "修正済み:" in text:
        text = text.split("修正済み:", 1)[0]
    return text.strip()


@router.post(
    "/api/workspaces/thumbnails/{channel}/{video}/comment-patch",
    response_model=ThumbnailCommentPatchResponse,
)
def get_thumbnail_comment_patch(channel: str, video: str, request: ThumbnailCommentPatchRequest):
    """
    Translate a human comment into a safe per-video thumb_spec patch (allowlist + validation).

    Output contract: `ytm.thumbnail.comment_patch.v1` (see ssot/plans/PLAN_THUMBNAILS_SCALE_SYSTEM.md).
    """
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    comment = _extract_thumbnail_human_comment(request.comment)
    if not comment:
        raise HTTPException(status_code=400, detail="comment is required")

    # Disabled: thumbnail tuning comments are processed in the operator chat (this conversation),
    # not via backend LLM translation.
    return ThumbnailCommentPatchResponse(
        schema=THUMBNAIL_COMMENT_PATCH_SCHEMA_V1,
        target=ThumbnailCommentPatchTargetResponse(channel=channel_code, video=video_number),
        confidence=0.0,
        clarifying_questions=[
            "コメントの解釈はこのチャットで実施します（UI/API では自動変換しません）。"
            "必要な調整は thumb_spec.json の overrides に落として保存してください。",
        ],
        ops=[],
        provider=None,
        model=None,
    )


@router.post(
    "/api/workspaces/thumbnails/{channel}/{video}/layer-specs/build",
    response_model=ThumbnailLayerSpecsBuildResponse,
)
def build_thumbnail_layer_specs(channel: str, video: str, request: ThumbnailLayerSpecsBuildRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    if request.regen_bg and not request.allow_generate:
        raise HTTPException(status_code=400, detail="regen_bg requires allow_generate=true")

    try:
        from script_pipeline.thumbnails.layers.image_layer import resolve_background_source
        from script_pipeline.thumbnails.tools.layer_specs_builder import BuildTarget, build_channel_thumbnails
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"layer_specs builder is not available: {exc}") from exc

    video_dir = THUMBNAIL_ASSETS_DIR / channel_code / video_number
    assets_root = THUMBNAIL_ASSETS_DIR / channel_code
    try:
        bg_source = resolve_background_source(video_dir=video_dir, channel_root=assets_root, video=video_number)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to resolve background source: {exc}") from exc

    if not request.allow_generate and bg_source.bg_src is None:
        raise HTTPException(
            status_code=400,
            detail="background not found; add 10_bg.* / 90_bg_ai_raw.* or set allow_generate=true",
        )

    build_id = f"ui_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    try:
        build_channel_thumbnails(
            channel=channel_code,
            targets=[BuildTarget(channel=channel_code, video=video_number)],
            width=1920,
            height=1080,
            force=True,
            skip_generate=not bool(request.allow_generate),
            continue_on_error=False,
            max_gen_attempts=2,
            export_flat=False,
            flat_name_suffix="",
            sleep_sec=0.2,
            bg_brightness=1.0,
            bg_contrast=1.0,
            bg_color=1.0,
            bg_gamma=1.0,
            bg_zoom=1.0,
            bg_pan_x=0.0,
            bg_pan_y=0.0,
            bg_band_brightness=1.0,
            bg_band_contrast=1.0,
            bg_band_color=1.0,
            bg_band_gamma=1.0,
            bg_band_x0=0.0,
            bg_band_x1=0.0,
            bg_band_power=1.0,
            regen_bg=bool(request.regen_bg),
            build_id=build_id,
            output_mode=str(request.output_mode),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"layer_specs build failed: {exc}") from exc

    thumb_path = f"{channel_code}/{video_number}/00_thumb.png"
    thumb_url = f"/thumbnails/assets/{channel_code}/{video_number}/00_thumb.png"
    meta_path = THUMBNAIL_ASSETS_DIR / channel_code / video_number / "compiler" / build_id / "build_meta.json"
    meta_rel = safe_relative_path(meta_path) if meta_path.exists() else None

    return ThumbnailLayerSpecsBuildResponse(
        status="ok",
        channel=channel_code,
        video=video_number,
        build_id=build_id,
        thumb_url=thumb_url,
        thumb_path=thumb_path,
        build_meta_path=meta_rel,
    )


@router.post(
    "/api/workspaces/thumbnails/{channel}/{video}/two-up/build",
    response_model=ThumbnailTwoUpBuildResponse,
)
def build_thumbnail_two_up(channel: str, video: str, request: ThumbnailLayerSpecsBuildRequest):
    """
    Build "stable" two-up outputs (00_thumb_1 / 00_thumb_2) for channels that ship both.

    Notes:
    - Reuses the standard layer_specs builder twice (00_thumb_1 / 00_thumb_2).
    """
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    if request.regen_bg and not request.allow_generate:
        raise HTTPException(status_code=400, detail="regen_bg requires allow_generate=true")

    try:
        from script_pipeline.thumbnails.layers.image_layer import resolve_background_source
        from script_pipeline.thumbnails.tools.layer_specs_builder import BuildTarget, build_channel_thumbnails
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"two-up builder is not available: {exc}") from exc

    video_dir = THUMBNAIL_ASSETS_DIR / channel_code / video_number
    assets_root = THUMBNAIL_ASSETS_DIR / channel_code
    try:
        bg_source = resolve_background_source(video_dir=video_dir, channel_root=assets_root, video=video_number)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to resolve background source: {exc}") from exc

    if not request.allow_generate and bg_source.bg_src is None:
        raise HTTPException(
            status_code=400,
            detail="background not found; add 10_bg.* / 90_bg_ai_raw.* or set allow_generate=true",
        )

    build_id_base = f"ui_two_up_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    try:
        for stable_thumb_name in ("00_thumb_1.png", "00_thumb_2.png"):
            stem = Path(stable_thumb_name).stem
            build_channel_thumbnails(
                channel=channel_code,
                targets=[BuildTarget(channel=channel_code, video=video_number)],
                width=1920,
                height=1080,
                stable_thumb_name=stable_thumb_name,
                variant_label=stem,
                force=True,
                skip_generate=not bool(request.allow_generate),
                continue_on_error=False,
                max_gen_attempts=2,
                export_flat=False,
                flat_name_suffix="",
                sleep_sec=0.2,
                bg_brightness=1.0,
                bg_contrast=1.0,
                bg_color=1.0,
                bg_gamma=1.0,
                bg_zoom=1.0,
                bg_pan_x=0.0,
                bg_pan_y=0.0,
                bg_band_brightness=1.0,
                bg_band_contrast=1.0,
                bg_band_color=1.0,
                bg_band_gamma=1.0,
                bg_band_x0=0.0,
                bg_band_x1=0.0,
                bg_band_power=1.0,
                regen_bg=bool(request.regen_bg),
                build_id=f"{build_id_base}__{stem}",
                output_mode=str(request.output_mode),
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"two-up build failed: {exc}") from exc

    # Keep a canonical 00_thumb.png for legacy views by copying thumb_1 when present.
    try:
        assets_dir = THUMBNAIL_ASSETS_DIR / channel_code / video_number
        src = assets_dir / "00_thumb_1.png"
        dst = assets_dir / "00_thumb.png"
        if src.exists():
            dst.write_bytes(src.read_bytes())
    except Exception:
        pass

    outputs = {
        "00_thumb_1": f"/thumbnails/assets/{channel_code}/{video_number}/00_thumb_1.png",
        "00_thumb_2": f"/thumbnails/assets/{channel_code}/{video_number}/00_thumb_2.png",
        "00_thumb": f"/thumbnails/assets/{channel_code}/{video_number}/00_thumb.png",
    }
    paths = {
        "00_thumb_1": f"{channel_code}/{video_number}/00_thumb_1.png",
        "00_thumb_2": f"{channel_code}/{video_number}/00_thumb_2.png",
        "00_thumb": f"{channel_code}/{video_number}/00_thumb.png",
    }
    return ThumbnailTwoUpBuildResponse(status="ok", channel=channel_code, video=video_number, outputs=outputs, paths=paths)


@router.post(
    "/api/workspaces/thumbnails/{channel}/{video}/variants",
    response_model=ThumbnailVariantResponse,
    status_code=201,
)
def create_thumbnail_variant_entry(channel: str, video: str, payload: ThumbnailVariantCreateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    label = payload.label.strip()
    variant = _persist_thumbnail_variant(
        channel_code,
        video_number,
        label=label,
        status=payload.status,
        image_url=payload.image_url,
        image_path=payload.image_path,
        notes=payload.notes,
        tags=payload.tags,
        prompt=payload.prompt,
        make_selected=bool(payload.make_selected),
    )
    return variant


@router.patch(
    "/api/workspaces/thumbnails/{channel}/{video}/variants/{variant_id}",
    response_model=ThumbnailVariantResponse,
)
def patch_thumbnail_variant_entry(channel: str, video: str, variant_id: str, payload: ThumbnailVariantPatchRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    vid = str(variant_id or "").strip()
    if not vid:
        raise HTTPException(status_code=400, detail="variant_id is required")

    updates: Dict[str, Any] = {}
    if payload.label is not None:
        label = str(payload.label or "").strip()
        if not label:
            raise HTTPException(status_code=400, detail="label cannot be empty")
        updates["label"] = label[:120]
    if payload.status is not None:
        updates["status"] = _normalize_thumbnail_status(payload.status)
    if payload.notes is not None:
        notes = str(payload.notes or "").strip()
        updates["notes"] = notes if notes else None
    if payload.tags is not None:
        updates["tags"] = _normalize_thumbnail_tags(payload.tags)
    make_selected = payload.make_selected

    if not updates and make_selected is None:
        raise HTTPException(status_code=400, detail="no updates specified")

    now = datetime.now(timezone.utc).isoformat()
    with THUMBNAIL_PROJECTS_LOCK:
        path, doc = _load_thumbnail_projects_document()
        project = _get_or_create_thumbnail_project(doc, channel_code, video_number)
        variants = project.get("variants") if isinstance(project.get("variants"), list) else []
        target: Optional[dict] = None
        for raw_variant in variants:
            if not isinstance(raw_variant, dict):
                continue
            if str(raw_variant.get("id") or "").strip() == vid:
                target = raw_variant
                break
        if target is None:
            raise HTTPException(status_code=404, detail="variant not found")

        if "label" in updates:
            target["label"] = updates["label"]
        if "status" in updates:
            target["status"] = updates["status"]
        if "notes" in updates:
            target["notes"] = updates["notes"]
        if "tags" in updates:
            target["tags"] = updates["tags"]

        target["updated_at"] = now
        if make_selected is True:
            project["selected_variant_id"] = vid
        elif make_selected is False:
            # Do not unset selected_variant_id automatically; explicit project PATCH should handle it.
            pass
        project["updated_at"] = now
        _write_thumbnail_projects_document(path, doc)

    selected_variant_id = str(project.get("selected_variant_id") or "").strip()
    is_selected = bool(selected_variant_id and selected_variant_id == vid)
    image_url = str(target.get("image_url") or "").strip() or None
    preview_url = str(target.get("preview_url") or "").strip() or image_url
    return ThumbnailVariantResponse(
        id=vid,
        label=str(target.get("label") or "").strip() or None,
        status=str(target.get("status") or "").strip() or None,
        image_url=image_url,
        image_path=str(target.get("image_path") or "").strip() or None,
        preview_url=preview_url,
        notes=str(target.get("notes") or "").strip() or None,
        tags=target.get("tags") if isinstance(target.get("tags"), list) else None,
        provider=str(target.get("provider") or "").strip() or None,
        model=str(target.get("model") or "").strip() or None,
        model_key=str(target.get("model_key") or "").strip() or None,
        openrouter_generation_id=str(target.get("openrouter_generation_id") or "").strip() or None,
        cost_usd=(float(target.get("cost_usd")) if target.get("cost_usd") is not None else None),
        usage=target.get("usage") if isinstance(target.get("usage"), dict) else None,
        is_selected=is_selected,
        created_at=str(target.get("created_at") or "").strip() or None,
        updated_at=str(target.get("updated_at") or "").strip() or None,
    )


@router.post(
    "/api/workspaces/thumbnails/{channel}/{video}/variants/generate",
    response_model=List[ThumbnailVariantResponse],
    status_code=201,
)
def generate_thumbnail_variant_images(channel: str, video: str, payload: ThumbnailVariantGenerateRequest):
    """
    Generate thumbnail images via ImageClient (OpenRouter/Gemini) and persist as variants.

    Notes:
    - Manual operation only (intended for UI / Swagger usage).
    - No automatic model fallback: the request must resolve to exactly one image model key.
    """
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)

    template: Optional[dict] = None
    template_name: str = ""
    template_id: Optional[str] = payload.template_id.strip() if payload.template_id else None

    if template_id or not (payload.prompt and payload.prompt.strip()):
        with THUMBNAIL_TEMPLATES_LOCK:
            _, doc = _load_thumbnail_templates_document()
            channels = doc.get("channels") if isinstance(doc, dict) else None
            channel_doc = channels.get(channel_code) if isinstance(channels, dict) else None
        if isinstance(channel_doc, dict):
            if not template_id:
                default_id = channel_doc.get("default_template_id")
                if isinstance(default_id, str) and default_id.strip():
                    template_id = default_id.strip()
            raw_templates = channel_doc.get("templates") or []
            if template_id and isinstance(raw_templates, list):
                for raw in raw_templates:
                    if not isinstance(raw, dict):
                        continue
                    if str(raw.get("id") or "").strip() == template_id:
                        template = raw
                        template_name = str(raw.get("name") or "").strip()
                        break

    if template_id and template is None:
        raise HTTPException(status_code=404, detail=f"template not found: {template_id}")

    model_key = payload.image_model_key.strip() if payload.image_model_key else ""
    if not model_key and isinstance(template, dict):
        model_key = str(template.get("image_model_key") or "").strip()
    if not model_key:
        raise HTTPException(status_code=400, detail="image_model_key is required (or set it in the template)")

    config_path = PROJECT_ROOT / "configs" / "image_models.yaml"
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            conf = yaml.safe_load(fh) or {}
        models = conf.get("models") if isinstance(conf, dict) else None
        if isinstance(models, dict) and model_key not in {str(key) for key in models.keys()}:
            raise HTTPException(status_code=400, detail=f"unknown image_model_key: {model_key}")
    except HTTPException:
        raise
    except Exception:
        # If config cannot be loaded, skip validation here (ImageClient will error if invalid).
        pass

    prompt = payload.prompt.strip() if payload.prompt else ""
    if not prompt:
        if not isinstance(template, dict):
            raise HTTPException(status_code=400, detail="prompt is required when no template is selected")
        template_text = str(template.get("prompt_template") or "")
        if not template_text.strip():
            raise HTTPException(status_code=400, detail="template.prompt_template is empty")
        ctx = _build_thumbnail_template_context(channel_code, video_number)
        prompt = _render_thumbnail_prompt_template(template_text, ctx).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is empty after rendering")

    label_base = payload.label.strip() if payload.label else ""
    notes = payload.notes.strip() if isinstance(payload.notes, str) and payload.notes.strip() else None
    tags = payload.tags

    try:
        from factory_common.image_client import ImageClient, ImageTaskOptions, ImageGenerationError
    except Exception as exc:  # pragma: no cover - optional dependency mismatch
        raise HTTPException(status_code=500, detail=f"ImageClient is not available: {exc}") from exc

    try:
        image_client = ImageClient()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ImageClient initialization failed: {exc}") from exc

    variants: List[ThumbnailVariantResponse] = []
    for idx in range(payload.count):
        try:
            result = image_client.generate(
                ImageTaskOptions(
                    task="thumbnail_image_gen",
                    prompt=prompt,
                    aspect_ratio="16:9",
                    n=1,
                    extra={"model_key": model_key},
                )
            )
        except ImageGenerationError as exc:
            raise HTTPException(status_code=502, detail=f"image generation failed: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"image generation failed: {exc}") from exc

        cost_usd: Optional[float] = None
        openrouter_generation_id: Optional[str] = None
        usage: Optional[Dict[str, Any]] = None
        if result.provider == "openrouter" and result.request_id:
            openrouter_generation_id = result.request_id
            gen = _fetch_openrouter_generation(result.request_id)
            if isinstance(gen, dict):
                openrouter_generation_id = str(gen.get("id") or "").strip() or result.request_id
                total_cost = gen.get("total_cost")
                if isinstance(total_cost, (int, float)):
                    cost_usd = float(total_cost)
                elif isinstance(total_cost, str):
                    try:
                        cost_usd = float(total_cost)
                    except ValueError:
                        cost_usd = None
                usage = {
                    "total_cost": cost_usd,
                    "native_tokens_prompt": gen.get("native_tokens_prompt"),
                    "native_tokens_completion": gen.get("native_tokens_completion"),
                    "native_tokens_completion_images": gen.get("native_tokens_completion_images"),
                }

        image_data = result.images[0] if result.images else None
        if not image_data:
            raise HTTPException(status_code=502, detail="image generation returned no image bytes")

        png_bytes = _normalize_thumbnail_image_bytes(image_data)

        dest_dir = THUMBNAIL_ASSETS_DIR / channel_code / video_number
        filename = f"ai_{uuid.uuid4().hex[:12]}.png"
        destination = _ensure_unique_filename(dest_dir, filename)
        try:
            destination.write_bytes(png_bytes)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"failed to write thumbnail asset: {exc}") from exc

        rel_path = f"{channel_code}/{video_number}/{destination.name}"
        label = label_base
        if not label:
            label = "AI"
            if template_name:
                label = f"{label} {template_name}"
            if payload.count > 1:
                label = f"{label} {idx + 1}"

        variant = _persist_thumbnail_variant(
            channel_code,
            video_number,
            label=label,
            status=payload.status,
            image_path=rel_path,
            notes=notes,
            tags=tags,
            prompt=prompt,
            provider=result.provider,
            model=result.model,
            model_key=model_key,
            openrouter_generation_id=openrouter_generation_id,
            cost_usd=cost_usd,
            usage=usage,
            make_selected=bool(payload.make_selected) and idx == 0,
        )
        variants.append(variant)

    return variants


@router.post(
    "/api/workspaces/thumbnails/{channel}/{video}/variants/compose",
    response_model=ThumbnailVariantResponse,
    status_code=201,
)
def compose_thumbnail_variant(channel: str, video: str, payload: ThumbnailVariantComposeRequest):
    """
    Compose a thumbnail locally (no AI).

    Uses:
    - base: `asset/thumbnails/CH12/ch12_buddha_bg_1536x1024.png` (flipped by default)
    - stylepack: `workspaces/thumbnails/compiler/stylepacks/{channel}_*.yaml`
    - copy: planning CSV (サムネタイトル上/サムネタイトル/サムネタイトル下) or payload overrides
    """
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)

    upper = payload.copy_upper.strip() if isinstance(payload.copy_upper, str) else ""
    title = payload.copy_title.strip() if isinstance(payload.copy_title, str) else ""
    lower = payload.copy_lower.strip() if isinstance(payload.copy_lower, str) else ""

    if not (upper and title and lower):
        try:
            for row in planning_store.get_rows(channel_code, force_refresh=True):
                if normalize_video_number(row.video_number or "") != video_number:
                    continue
                raw = row.raw if isinstance(row.raw, dict) else {}
                if not upper:
                    upper = str(raw.get("サムネタイトル上") or "").strip()
                if not title:
                    title = str(raw.get("サムネタイトル") or "").strip()
                if not lower:
                    lower = str(raw.get("サムネタイトル下") or "").strip()
                break
        except Exception:
            pass

    if not (upper and title and lower):
        raise HTTPException(status_code=400, detail="企画CSVのサムネコピー（上/中/下）が必要です。")

    label = payload.label.strip() if isinstance(payload.label, str) and payload.label.strip() else "文字合成"
    notes = payload.notes.strip() if isinstance(payload.notes, str) and payload.notes.strip() else None
    tags = payload.tags

    base_path = ssot_assets_root() / "thumbnails" / "CH12" / "ch12_buddha_bg_1536x1024.png"
    if not base_path.exists():
        raise HTTPException(status_code=500, detail=f"base image not found: {base_path}")

    try:
        from script_pipeline.thumbnails.compiler import compile_buddha_3line as compiler
        from script_pipeline.thumbnails.io_utils import save_png_atomic
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"thumbnail compiler is not available: {exc}") from exc

    try:
        stylepack = compiler._load_stylepack(channel_code)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to load stylepack: {exc}") from exc

    try:
        font_path = compiler.resolve_font_path(None)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    flip_base = True if payload.flip_base is None else bool(payload.flip_base)
    impact = True if payload.impact is None else bool(payload.impact)

    build_id = datetime.now(timezone.utc).strftime("ui_%Y%m%dT%H%M%SZ")
    out_dir = THUMBNAIL_ASSETS_DIR / channel_code / video_number / "compiler" / build_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_img_path = out_dir / "out_01.png"
    out_meta_path = out_dir / "build_meta.json"

    try:
        img = compiler.compose_buddha_3line(
            base_image_path=base_path,
            stylepack=stylepack,
            text=compiler.ThumbText(upper=upper, title=title, lower=lower),
            font_path=font_path,
            flip_base=flip_base,
            impact=impact,
            belt_override=False,
        )
        save_png_atomic(img.convert("RGB"), out_img_path, mode="draft", verify=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to compose thumbnail: {exc}") from exc

    try:
        meta = {
            "schema": "ytm.thumbnail.compiler.build.v1",
            "source": "ui",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "channel": channel_code,
            "video": video_number,
            "build_id": build_id,
            "output_mode": "draft",
            "stylepack_id": stylepack.get("id"),
            "stylepack_path": stylepack.get("_stylepack_path"),
            "base_image": str(base_path),
            "flip_base": flip_base,
            "impact": impact,
            "belt_enabled": False,
            "text": {"upper": upper, "title": title, "lower": lower},
            "output": {"image": str(out_img_path)},
        }
        tmp_meta = out_meta_path.with_suffix(out_meta_path.suffix + ".tmp")
        tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_meta.replace(out_meta_path)
    except Exception:
        # best-effort: meta is optional
        pass

    rel_path = f"{channel_code}/{video_number}/compiler/{build_id}/{out_img_path.name}"
    variant = _persist_thumbnail_variant(
        channel_code,
        video_number,
        label=label,
        status=payload.status,
        image_path=rel_path,
        notes=notes,
        tags=tags,
        make_selected=bool(payload.make_selected),
    )
    return variant


@router.post(
    "/api/workspaces/thumbnails/{channel}/{video}/variants/upload",
    response_model=ThumbnailVariantResponse,
    status_code=201,
)
async def upload_thumbnail_variant_asset(
    channel: str,
    video: str,
    file: UploadFile = File(...),
    label: Optional[str] = Form(default=None),
    status: Optional[str] = Form(default="draft"),
    make_selected: Optional[bool] = Form(default=False),
    notes: Optional[str] = Form(default=None),
    tags: Optional[str] = Form(default=None),
):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="画像ファイルを指定してください。")
    sanitized_name = _sanitize_library_filename(file.filename, default_prefix="thumbnail")
    dest_dir = THUMBNAIL_ASSETS_DIR / channel_code / video_number
    destination = _ensure_unique_filename(dest_dir, sanitized_name)
    await _save_upload_file(file, destination)
    tags_payload: Optional[List[str]] = None
    if tags:
        try:
            parsed = json.loads(tags)
            if isinstance(parsed, list):
                tags_payload = [str(item) for item in parsed if isinstance(item, str)]
        except json.JSONDecodeError:
            tags_payload = None
    rel_path = f"{channel_code}/{video_number}/{destination.name}"
    variant = _persist_thumbnail_variant(
        channel_code,
        video_number,
        label=(label or Path(destination.name).stem),
        status=status,
        image_path=rel_path,
        notes=notes,
        tags=tags_payload,
        make_selected=bool(make_selected),
    )
    return variant


@router.post(
    "/api/workspaces/thumbnails/{channel}/{video}/assets/{slot}",
    response_model=ThumbnailAssetReplaceResponse,
)
async def replace_thumbnail_video_asset(
    channel: str,
    video: str,
    slot: str,
    file: UploadFile = File(...),
):
    """
    Replace a canonical per-video thumbnail asset (e.g. 10_bg / 00_thumb_1).

    Intended for manual operations in UI:
    - Swap in a PNG exported from CapCut, etc.
    - Keep stable filenames (00_thumb_1.png) so downstream ZIP/download remains consistent.
    """
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    slot_key = str(slot or "").strip()
    if not slot_key:
        raise HTTPException(status_code=400, detail="slot is required")

    cleaned = slot_key.split("?", 1)[0].split("#", 1)[0].strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="slot is required")
    base = Path(cleaned).name.strip()
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    lowered = base.strip().lower()

    slot_alias = {
        "bg": "10_bg",
        "background": "10_bg",
        "10_bg": "10_bg",
        "portrait": "20_portrait",
        "20_portrait": "20_portrait",
        "bg_ai_raw": "90_bg_ai_raw",
        "90_bg_ai_raw": "90_bg_ai_raw",
        # Canonical output (single).
        "00_thumb": "00_thumb",
        "thumb": "00_thumb",
        # Two-up stable outputs (aliases).
        "00_thumb_1": "00_thumb_1",
        "thumb_1": "00_thumb_1",
        "thumb1": "00_thumb_1",
        "a": "00_thumb_1",
        "1": "00_thumb_1",
        "00_thumb_2": "00_thumb_2",
        "thumb_2": "00_thumb_2",
        "thumb2": "00_thumb_2",
        "b": "00_thumb_2",
        "2": "00_thumb_2",
    }

    normalized_slot = slot_alias.get(lowered) or slot_alias.get(slot_key.strip().lower())
    slot_to_filename = {
        "10_bg": "10_bg.png",
        "20_portrait": "20_portrait.png",
        "90_bg_ai_raw": "90_bg_ai_raw.png",
        "00_thumb": "00_thumb.png",
        "00_thumb_1": "00_thumb_1.png",
        "00_thumb_2": "00_thumb_2.png",
    }
    if normalized_slot not in slot_to_filename:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported slot: {slot_key} (supported: {sorted(slot_to_filename.keys())})",
        )

    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="画像ファイルを指定してください。")

    # Validate extension early (content is verified by PIL during normalization).
    _sanitize_library_filename(file.filename, default_prefix=normalized_slot)

    raw_bytes = await file.read()
    await file.seek(0)
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="empty file")

    filename = slot_to_filename[normalized_slot]
    dest_dir = THUMBNAIL_ASSETS_DIR / channel_code / video_number
    dest_dir.mkdir(parents=True, exist_ok=True)
    destination = dest_dir / filename

    if normalized_slot == "20_portrait":
        try:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                out = io.BytesIO()
                img.convert("RGBA").save(out, format="PNG", optimize=True)
                png_bytes = out.getvalue()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"画像の読み込みに失敗しました: {exc}") from exc
    else:
        png_bytes = _normalize_thumbnail_image_bytes(raw_bytes, width=1920, height=1080)

    tmp = destination.with_suffix(destination.suffix + ".tmp")
    try:
        tmp.write_bytes(png_bytes)
        tmp.replace(destination)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to write thumbnail asset: {exc}") from exc

    # Keep canonical 00_thumb.png in sync for two-up channels (legacy views).
    if normalized_slot == "00_thumb_1":
        try:
            canonical = dest_dir / "00_thumb.png"
            tmp_canonical = canonical.with_suffix(canonical.suffix + ".tmp")
            tmp_canonical.write_bytes(png_bytes)
            tmp_canonical.replace(canonical)
        except Exception:
            pass

    rel_path = f"{channel_code}/{video_number}/{destination.name}"
    public_url = f"/thumbnails/assets/{rel_path}"
    return ThumbnailAssetReplaceResponse(
        status="ok",
        channel=channel_code,
        video=video_number,
        slot=normalized_slot,
        file_name=destination.name,
        image_path=rel_path,
        public_url=public_url,
    )
