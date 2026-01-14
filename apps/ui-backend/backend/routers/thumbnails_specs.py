from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException

from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.app.thumbnails_layer_specs_models import (
    ThumbnailChannelLayerSpecsResponse,
    ThumbnailLayerSpecPlanningSuggestionsResponse,
    ThumbnailLayerSpecRefResponse,
    ThumbnailParamCatalogEntryResponse,
    ThumbnailVideoLayerSpecsResponse,
    ThumbnailVideoTextLayoutSpecResponse,
)

router = APIRouter(prefix="/api/workspaces/thumbnails", tags=["thumbnails"])


def _to_layer_spec_ref(spec_id: Optional[str]) -> Optional[ThumbnailLayerSpecRefResponse]:
    if not isinstance(spec_id, str) or not spec_id.strip():
        return None
    try:
        from script_pipeline.thumbnails.compiler.layer_specs import resolve_layer_spec_ref

        ref = resolve_layer_spec_ref(spec_id.strip())
        return ThumbnailLayerSpecRefResponse(
            id=ref.spec_id,
            kind=ref.kind,
            version=int(ref.version),
            path=ref.path,
            name=ref.name,
        )
    except Exception:
        return None


@router.get(
    "/{channel}/layer-specs",
    response_model=ThumbnailChannelLayerSpecsResponse,
)
def get_thumbnail_channel_layer_specs(channel: str):
    channel_code = normalize_channel_code(channel)
    try:
        from script_pipeline.thumbnails.compiler.layer_specs import resolve_channel_layer_spec_ids

        image_prompts_id, text_layout_id = resolve_channel_layer_spec_ids(channel_code)
    except Exception:
        image_prompts_id, text_layout_id = (None, None)

    return ThumbnailChannelLayerSpecsResponse(
        channel=channel_code,
        image_prompts=_to_layer_spec_ref(image_prompts_id),
        text_layout=_to_layer_spec_ref(text_layout_id),
    )


@router.get(
    "/{channel}/{video}/layer-specs",
    response_model=ThumbnailVideoLayerSpecsResponse,
)
def get_thumbnail_video_layer_specs(channel: str, video: str):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    video_id = f"{channel_code}-{video_number}"

    try:
        from script_pipeline.thumbnails.compiler.layer_specs import (
            find_image_prompt_for_video,
            find_text_layout_item_for_video,
            load_layer_spec_yaml,
            resolve_channel_layer_spec_ids,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"layer_specs module is not available: {exc}") from exc

    image_prompts_id, text_layout_id = resolve_channel_layer_spec_ids(channel_code)

    image_prompt: Optional[str] = None
    if isinstance(image_prompts_id, str) and image_prompts_id.strip():
        try:
            spec = load_layer_spec_yaml(image_prompts_id.strip())
            image_prompt = find_image_prompt_for_video(spec, video_id)
        except Exception:
            image_prompt = None

    text_layout_payload: Optional[ThumbnailVideoTextLayoutSpecResponse] = None
    suggestion_upper: Optional[str] = None
    suggestion_title: Optional[str] = None
    suggestion_lower: Optional[str] = None
    suggestion_design: Optional[str] = None

    if isinstance(text_layout_id, str) and text_layout_id.strip():
        try:
            spec = load_layer_spec_yaml(text_layout_id.strip())
            item = find_text_layout_item_for_video(spec, video_id)
            if isinstance(item, dict):
                template_id = str(item.get("template_id") or "").strip() or None
                fallbacks_raw = item.get("fallbacks")
                fallbacks: Optional[List[str]] = None
                if isinstance(fallbacks_raw, list):
                    fallbacks = [str(x).strip() for x in fallbacks_raw if isinstance(x, str) and str(x).strip()] or None
                text_raw = item.get("text")
                text: Optional[Dict[str, str]] = None
                if isinstance(text_raw, dict):
                    text = {str(k): str(v) for k, v in text_raw.items() if isinstance(v, str)}

                text_layout_payload = ThumbnailVideoTextLayoutSpecResponse(
                    template_id=template_id,
                    fallbacks=fallbacks,
                    text=text,
                )

                if text:
                    suggestion_upper = (text.get("top") or "").strip() or None
                    suggestion_title = (text.get("main") or "").strip() or None
                    suggestion_lower = (text.get("accent") or "").strip() or None

                if template_id:
                    desc = None
                    templates = spec.get("templates")
                    if isinstance(templates, dict):
                        tpl = templates.get(template_id)
                        if isinstance(tpl, dict) and isinstance(tpl.get("description"), str):
                            desc = tpl.get("description")
                    suggestion_design = f"layer_specs:{text_layout_id.strip()} template={template_id}"
                    if isinstance(desc, str) and desc.strip():
                        suggestion_design = f"{suggestion_design} ({desc.strip()})"
        except Exception:
            text_layout_payload = None

    planning_suggestions: Optional[ThumbnailLayerSpecPlanningSuggestionsResponse] = None
    if image_prompt or suggestion_upper or suggestion_title or suggestion_lower or suggestion_design:
        planning_suggestions = ThumbnailLayerSpecPlanningSuggestionsResponse(
            thumbnail_prompt=image_prompt,
            thumbnail_upper=suggestion_upper,
            thumbnail_title=suggestion_title,
            thumbnail_lower=suggestion_lower,
            text_design_note=suggestion_design,
        )

    return ThumbnailVideoLayerSpecsResponse(
        channel=channel_code,
        video=video_number,
        video_id=video_id,
        image_prompt=image_prompt,
        text_layout=text_layout_payload,
        planning_suggestions=planning_suggestions,
    )


@router.get(
    "/param-catalog",
    response_model=List[ThumbnailParamCatalogEntryResponse],
)
def get_thumbnail_param_catalog():
    try:
        from script_pipeline.thumbnails.param_catalog_v1 import PARAM_CATALOG_V1
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"param catalog module is not available: {exc}") from exc

    items: List[ThumbnailParamCatalogEntryResponse] = []
    for path, spec in sorted(PARAM_CATALOG_V1.items(), key=lambda kv: str(kv[0])):
        items.append(
            ThumbnailParamCatalogEntryResponse(
                path=str(path),
                kind=str(spec.kind),
                engine=str(spec.engine),
                min_value=(float(spec.min_value) if spec.min_value is not None else None),
                max_value=(float(spec.max_value) if spec.max_value is not None else None),
            )
        )
    return items
