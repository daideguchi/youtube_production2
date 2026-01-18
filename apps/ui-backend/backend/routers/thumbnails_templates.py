from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import uuid
import yaml
from fastapi import APIRouter, HTTPException

from backend.app.llm_catalog_store import _get_openrouter_pricing_by_model_id
from backend.app.normalize import normalize_channel_code
from backend.app.thumbnails_layer_specs_models import ThumbnailImageModelInfoResponse
from backend.app.thumbnails_templates_models import (
    ThumbnailChannelStyleResponse,
    ThumbnailChannelTemplatesResponse,
    ThumbnailChannelTemplatesUpdateRequest,
    ThumbnailTemplateResponse,
)
from backend.app.thumbnails_templates_store import (
    THUMBNAIL_TEMPLATES_LOCK,
    _load_thumbnail_templates_document,
    _write_thumbnail_templates_document,
)
from backend.app.path_utils import PROJECT_ROOT

router = APIRouter(prefix="/api/workspaces/thumbnails", tags=["thumbnails"])


@router.get(
    "/image-models",
    response_model=List[ThumbnailImageModelInfoResponse],
)
def list_thumbnail_image_models():
    """
    List available image model keys from `configs/image_models.yaml`.

    Intended for UI/template configuration (manual operation only).
    """
    config_path = PROJECT_ROOT / "configs" / "image_models.yaml"
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            conf = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image model config not found")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to load image model config: {exc}") from exc

    models = conf.get("models") if isinstance(conf, dict) else None
    if not isinstance(models, dict):
        return []

    pricing_by_id: Dict[str, Dict[str, str]] = {}
    pricing_updated_at: Optional[str] = None
    try:
        pricing_by_id, fetched_at = _get_openrouter_pricing_by_model_id()
        if fetched_at:
            pricing_updated_at = datetime.fromtimestamp(fetched_at, timezone.utc).isoformat()
    except Exception:
        pricing_by_id = {}
        pricing_updated_at = None

    out: List[ThumbnailImageModelInfoResponse] = []
    for key, model_conf in sorted(models.items(), key=lambda kv: str(kv[0])):
        if not isinstance(model_conf, dict):
            continue
        provider = str(model_conf.get("provider") or "").strip()
        model_name = str(model_conf.get("model_name") or "").strip()
        if not provider or not model_name:
            continue
        model_pricing: Optional[Dict[str, str]] = None
        model_pricing_updated_at: Optional[str] = None
        if provider == "openrouter" and pricing_updated_at:
            model_pricing = pricing_by_id.get(model_name)
            if model_pricing:
                model_pricing_updated_at = pricing_updated_at
        out.append(
            ThumbnailImageModelInfoResponse(
                key=str(key),
                provider=provider,
                model_name=model_name,
                pricing=model_pricing,
                pricing_updated_at=model_pricing_updated_at,
            )
        )
    return out


@router.get(
    "/{channel}/templates",
    response_model=ThumbnailChannelTemplatesResponse,
)
def get_thumbnail_channel_templates(channel: str):
    channel_code = normalize_channel_code(channel)
    with THUMBNAIL_TEMPLATES_LOCK:
        _path, payload = _load_thumbnail_templates_document()
        channels = payload.get("channels") if isinstance(payload, dict) else None
        channel_payload = channels.get(channel_code) if isinstance(channels, dict) else None

    if not isinstance(channel_payload, dict):
        channel_payload = {}

    raw_templates = channel_payload.get("templates") or []
    templates: List[ThumbnailTemplateResponse] = []
    for raw in raw_templates:
        if not isinstance(raw, dict):
            continue
        template_id = str(raw.get("id") or "").strip()
        if not template_id:
            continue
        templates.append(
            ThumbnailTemplateResponse(
                id=template_id,
                name=raw.get("name") or "",
                image_model_key=raw.get("image_model_key") or "",
                prompt_template=raw.get("prompt_template") or "",
                negative_prompt=raw.get("negative_prompt"),
                notes=raw.get("notes"),
                created_at=raw.get("created_at"),
                updated_at=raw.get("updated_at"),
            )
        )

    templates.sort(key=lambda tpl: (tpl.updated_at or "", tpl.created_at or "", tpl.name), reverse=True)
    template_ids = {tpl.id for tpl in templates}

    default_template_id = channel_payload.get("default_template_id")
    if isinstance(default_template_id, str):
        default_template_id = default_template_id.strip() or None
    else:
        default_template_id = None
    if default_template_id and default_template_id not in template_ids:
        default_template_id = None

    raw_style = channel_payload.get("channel_style") if isinstance(channel_payload, dict) else None
    channel_style: Optional[ThumbnailChannelStyleResponse] = None
    if isinstance(raw_style, dict):
        rules_payload = raw_style.get("rules")
        rules: Optional[List[str]] = None
        if isinstance(rules_payload, list):
            filtered = [str(item).strip() for item in rules_payload if isinstance(item, str) and str(item).strip()]
            rules = filtered or None
        channel_style = ThumbnailChannelStyleResponse(
            name=(str(raw_style.get("name")).strip() if isinstance(raw_style.get("name"), str) else None),
            benchmark_path=(
                str(raw_style.get("benchmark_path")).strip() if isinstance(raw_style.get("benchmark_path"), str) else None
            ),
            preview_upper=(
                str(raw_style.get("preview_upper")).strip() if isinstance(raw_style.get("preview_upper"), str) else None
            ),
            preview_title=(
                str(raw_style.get("preview_title")).strip() if isinstance(raw_style.get("preview_title"), str) else None
            ),
            preview_lower=(
                str(raw_style.get("preview_lower")).strip() if isinstance(raw_style.get("preview_lower"), str) else None
            ),
            rules=rules,
        )

    return ThumbnailChannelTemplatesResponse(
        channel=channel_code,
        default_template_id=default_template_id,
        templates=templates,
        channel_style=channel_style,
    )


@router.put(
    "/{channel}/templates",
    response_model=ThumbnailChannelTemplatesResponse,
)
def upsert_thumbnail_channel_templates(channel: str, request: ThumbnailChannelTemplatesUpdateRequest):
    channel_code = normalize_channel_code(channel)

    model_keys: set[str] = set()
    config_path = PROJECT_ROOT / "configs" / "image_models.yaml"
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            conf = yaml.safe_load(fh) or {}
        models = conf.get("models") if isinstance(conf, dict) else None
        if isinstance(models, dict):
            model_keys = {str(key) for key in models.keys()}
    except Exception:
        model_keys = set()

    now = datetime.now(timezone.utc).isoformat()

    with THUMBNAIL_TEMPLATES_LOCK:
        path, payload = _load_thumbnail_templates_document()
        channels = payload.get("channels")
        if not isinstance(channels, dict):
            channels = {}
            payload["channels"] = channels

        existing_by_id: Dict[str, dict] = {}
        existing_channel = channels.get(channel_code)
        if isinstance(existing_channel, dict):
            for raw in existing_channel.get("templates") or []:
                if not isinstance(raw, dict):
                    continue
                template_id = str(raw.get("id") or "").strip()
                if template_id:
                    existing_by_id[template_id] = raw

        templates_out: List[dict] = []
        seen_ids: set[str] = set()
        for tpl in request.templates:
            template_id = (tpl.id or "").strip()
            if not template_id:
                template_id = f"tmpl::{uuid.uuid4().hex[:12]}"
            if template_id in seen_ids:
                raise HTTPException(status_code=400, detail=f"duplicate template id: {template_id}")
            seen_ids.add(template_id)

            model_key = (tpl.image_model_key or "").strip()
            if model_keys and model_key not in model_keys:
                raise HTTPException(status_code=400, detail=f"unknown image_model_key: {model_key}")

            existing = existing_by_id.get(template_id, {})
            created_at = existing.get("created_at") or now
            templates_out.append(
                {
                    "id": template_id,
                    "name": tpl.name.strip(),
                    "image_model_key": model_key,
                    "prompt_template": tpl.prompt_template,
                    "negative_prompt": tpl.negative_prompt,
                    "notes": tpl.notes,
                    "created_at": created_at,
                    "updated_at": now,
                }
            )

        default_template_id = request.default_template_id
        if isinstance(default_template_id, str):
            default_template_id = default_template_id.strip() or None
        else:
            default_template_id = None
        if default_template_id and default_template_id not in seen_ids:
            raise HTTPException(status_code=400, detail="default_template_id not found in templates")

        merged_channel: Dict[str, Any] = dict(existing_channel) if isinstance(existing_channel, dict) else {}
        merged_channel.update(
            {
                "default_template_id": default_template_id,
                "templates": templates_out,
            }
        )
        channels[channel_code] = merged_channel
        _write_thumbnail_templates_document(path, payload)

    return get_thumbnail_channel_templates(channel_code)
