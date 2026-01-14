from __future__ import annotations

"""
Image model routing handlers for UI settings.

created: 2026-01-14
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml
from fastapi import HTTPException

from backend.app.codex_settings_store import _deep_merge_dict
from backend.app.image_model_routing_models import (
    ChannelImageModelRouting,
    ImageModelCatalogOption,
    ImageModelKeyInfo,
    ImageModelRoutingCatalog,
    ImageModelRoutingResponse,
    ImageModelRoutingSelection,
    ImageModelRoutingUpdate,
)
from backend.app.image_model_routing_policy import IMAGE_MODEL_KEY_BLOCKLIST, _image_model_key_blocked
from backend.app.normalize import normalize_channel_code
from backend.app.thumbnails_templates_store import (
    THUMBNAIL_TEMPLATES_LOCK,
    _load_thumbnail_templates_document,
    _write_thumbnail_templates_document,
)
from backend.app.video_channel_presets_store import (
    VIDEO_CHANNEL_PRESETS_LOCK,
    _load_video_channel_presets_document,
    _write_video_channel_presets_document,
)
from factory_common.paths import planning_root as ssot_planning_root
from factory_common.paths import repo_root as ssot_repo_root

PROJECT_ROOT = ssot_repo_root()
CHANNEL_PLANNING_DIR = ssot_planning_root() / "channels"


def _utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_image_models_index_simple() -> Dict[str, Dict[str, str]]:
    """
    Return {model_key: {provider, model_name}} from configs/image_models.yaml.

    This is used for UI selection only (manual operation).
    """
    config_path = PROJECT_ROOT / "configs" / "image_models.yaml"
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            conf = yaml.safe_load(fh) or {}
    except Exception:
        return {}

    models = conf.get("models") if isinstance(conf, dict) else None
    if not isinstance(models, dict):
        return {}

    out: Dict[str, Dict[str, str]] = {}
    for raw_key, model_conf in models.items():
        if not isinstance(model_conf, dict):
            continue
        provider = str(model_conf.get("provider") or "").strip()
        model_name = str(model_conf.get("model_name") or "").strip()
        key = str(raw_key or "").strip()
        if not key or not provider or not model_name:
            continue
        out[key] = {"provider": provider, "model_name": model_name}
    return out


def _load_image_model_slots_config() -> Dict[str, Any]:
    """
    Load optional image model slot codes (e.g. g-1 / f-4) for UI routing.

    Base: `configs/image_model_slots.yaml`
    Local: `configs/image_model_slots.local.yaml` (override; not tracked)
    """
    base_path = PROJECT_ROOT / "configs" / "image_model_slots.yaml"
    local_path = PROJECT_ROOT / "configs" / "image_model_slots.local.yaml"

    base: Dict[str, Any] = {"schema_version": 1, "slots": {}}
    if base_path.exists():
        try:
            with base_path.open("r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            if isinstance(loaded, dict):
                base = _deep_merge_dict(base, loaded)
        except Exception:
            pass

    if local_path.exists():
        try:
            with local_path.open("r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            if isinstance(loaded, dict) and loaded:
                return _deep_merge_dict(base, loaded)
        except Exception:
            pass

    return base


def _resolve_image_model_slot_selector(
    selector: str,
    *,
    task: str,
    slots_conf: Dict[str, Any],
) -> Optional[tuple[str, Dict[str, Any]]]:
    """
    Resolve slot selector -> real model_key.

    Returns:
      (resolved_model_key, meta)
    """
    raw = str(selector or "").strip()
    if not raw:
        return None

    slots = slots_conf.get("slots") if isinstance(slots_conf, dict) else None
    if not isinstance(slots, dict):
        return None
    ent = slots.get(raw)
    if ent is None and raw.lower() in slots:
        ent = slots.get(raw.lower())
    if not isinstance(ent, dict):
        return None

    tasks = ent.get("tasks")
    if not isinstance(tasks, dict):
        return None
    tn = str(task or "").strip()
    mk = tasks.get(tn)
    if mk in (None, ""):
        mk = tasks.get("default")
    if not isinstance(mk, str) or not mk.strip():
        return None

    mk_norm = mk.strip()
    meta: Dict[str, Any] = {
        "slot_code": raw,
        "resolved_model_key": mk_norm,
        "slot_label": str(ent.get("label") or "").strip() or None,
        "slot_description": str(ent.get("description") or "").strip() or None,
        "slot_task": tn,
    }
    return mk_norm, meta


def _list_planning_channel_codes() -> List[str]:
    """
    Enumerate channels based on Planning SoT (workspaces/planning/channels/CHxx.csv).
    """
    out: List[str] = []
    if not CHANNEL_PLANNING_DIR.exists():
        return out
    for path in sorted(CHANNEL_PLANNING_DIR.glob("CH*.csv")):
        code = str(path.stem or "").strip().upper()
        if len(code) == 4 and code.startswith("CH") and code[2:].isdigit():
            out.append(code)
    # de-dup while preserving order
    seen: set[str] = set()
    uniq: List[str] = []
    for code in out:
        if code in seen:
            continue
        seen.add(code)
        uniq.append(code)
    return uniq


def _resolve_image_model_key_info(
    model_key: Optional[str],
    *,
    index: Dict[str, Dict[str, str]],
    task: Optional[str] = None,
    slots_conf: Optional[Dict[str, Any]] = None,
) -> tuple[Optional[str], Optional[str], bool, Optional[str], Dict[str, Any]]:
    mk = str(model_key or "").strip()
    if not mk:
        return None, None, False, None, {}
    if _image_model_key_blocked(mk, task=task):
        return None, None, True, f"blocked model key: {mk}", {"blocked": True, "model_key": mk}

    meta = index.get(mk)
    if isinstance(meta, dict):
        provider = str(meta.get("provider") or "").strip() or None
        model_name = str(meta.get("model_name") or "").strip() or None
        return provider, model_name, False, None, {"resolved_model_key": mk}

    if task and slots_conf:
        resolved = _resolve_image_model_slot_selector(mk, task=str(task), slots_conf=slots_conf)
        if resolved is not None:
            resolved_key, slot_meta = resolved
            if _image_model_key_blocked(resolved_key, task=task):
                return None, None, True, f"blocked model key: {resolved_key}", slot_meta
            meta2 = index.get(resolved_key)
            if isinstance(meta2, dict):
                provider = str(meta2.get("provider") or "").strip() or None
                model_name = str(meta2.get("model_name") or "").strip() or None
                return provider, model_name, False, None, slot_meta
            return None, None, False, f"slot resolves to unknown model key: {resolved_key}", slot_meta

    return None, None, False, f"unknown model key: {mk}", {"missing_model_key": mk}


def _resolve_thumbnail_model_selection(
    channel_code: str,
    *,
    templates_doc: dict,
    model_index: Dict[str, Dict[str, str]],
    slots_conf: Dict[str, Any],
) -> ImageModelRoutingSelection:
    channels = templates_doc.get("channels") if isinstance(templates_doc, dict) else None
    channel_doc = channels.get(channel_code) if isinstance(channels, dict) else None
    if not isinstance(channel_doc, dict):
        return ImageModelRoutingSelection(
            model_key=None,
            provider=None,
            model_name=None,
            source="templates.json:missing_channel",
            missing=True,
            note="templates.json にチャンネル設定がありません（未初期化）",
        )

    raw_templates = channel_doc.get("templates")
    templates = raw_templates if isinstance(raw_templates, list) else []
    default_template_id = str(channel_doc.get("default_template_id") or "").strip() or None

    chosen: Optional[dict] = None
    source = "templates.json:missing_template"
    if default_template_id:
        for tpl in templates:
            if not isinstance(tpl, dict):
                continue
            if str(tpl.get("id") or "").strip() == default_template_id:
                chosen = tpl
                source = "templates.json:default_template_id"
                break
    if chosen is None and templates:
        chosen = next(
            (
                tpl
                for tpl in templates
                if isinstance(tpl, dict) and str(tpl.get("image_model_key") or "").strip()
            ),
            None,
        ) or next((tpl for tpl in templates if isinstance(tpl, dict)), None)
        if chosen is not None:
            source = "templates.json:first_template"

    if chosen is None:
        return ImageModelRoutingSelection(
            model_key=None,
            provider=None,
            model_name=None,
            source=source,
            missing=True,
            note="templates.json の templates が空です（未初期化）",
            meta={"default_template_id": default_template_id},
        )

    template_id = str(chosen.get("id") or "").strip() or None
    template_name = str(chosen.get("name") or "").strip() or None
    mk = str(chosen.get("image_model_key") or "").strip() or None
    provider, model_name, blocked, note, key_meta = _resolve_image_model_key_info(
        mk,
        index=model_index,
        task="thumbnail_image_gen",
        slots_conf=slots_conf,
    )
    missing = mk is None
    if missing and note is None:
        note = "image_model_key が未設定です"

    return ImageModelRoutingSelection(
        model_key=mk,
        provider=provider,
        model_name=model_name,
        source=source,
        missing=missing,
        blocked=blocked,
        note=note,
        meta={
            **(key_meta or {}),
            "template_id": template_id,
            "template_name": template_name,
            "default_template_id": default_template_id,
        },
    )


def _resolve_video_image_model_selection(
    channel_code: str,
    *,
    channel_presets_doc: dict,
    model_index: Dict[str, Dict[str, str]],
    slots_conf: Dict[str, Any],
) -> ImageModelRoutingSelection:
    channels = channel_presets_doc.get("channels") if isinstance(channel_presets_doc, dict) else None
    entry = channels.get(channel_code) if isinstance(channels, dict) else None
    if not isinstance(entry, dict):
        return ImageModelRoutingSelection(
            model_key=None,
            provider=None,
            model_name=None,
            source="channel_presets.json:missing_channel",
            missing=True,
            note="channel_presets.json にチャンネル設定がありません（未初期化）",
        )

    image_generation = entry.get("image_generation") if isinstance(entry.get("image_generation"), dict) else {}
    mk = str(image_generation.get("model_key") or "").strip() or None
    provider, model_name, blocked, note, key_meta = _resolve_image_model_key_info(
        mk,
        index=model_index,
        task="visual_image_gen",
        slots_conf=slots_conf,
    )
    missing = mk is None
    if missing and note is None:
        note = "image_generation.model_key が未設定です（tier default を使用）"

    return ImageModelRoutingSelection(
        model_key=mk,
        provider=provider,
        model_name=model_name,
        source="channel_presets.json:image_generation.model_key",
        missing=missing,
        blocked=blocked,
        note=note,
        meta={
            **(key_meta or {}),
            "preset_name": str(entry.get("name") or "").strip() or None,
            "status": str(entry.get("status") or "").strip() or None,
        },
    )


def _build_image_model_routing_catalog(
    model_index: Dict[str, Dict[str, str]],
    *,
    slots_conf: Dict[str, Any],
) -> ImageModelRoutingCatalog:
    known_keys = set(model_index.keys())

    def _enabled(model_key: Optional[str]) -> bool:
        if not model_key:
            return False
        if model_key in IMAGE_MODEL_KEY_BLOCKLIST:
            return False
        return model_key in known_keys

    def _opt(
        *,
        id: str,
        label: str,
        provider_group: str,
        variant: str,
        model_key: Optional[str],
        enabled: bool,
        note: Optional[str] = None,
    ) -> ImageModelCatalogOption:
        return ImageModelCatalogOption(
            id=id,
            label=label,
            provider_group=provider_group,
            variant=variant,
            model_key=model_key,
            enabled=enabled,
            note=note,
        )

    def _mk_opt(model_key: Optional[str], *, fallback_note: str, task: str) -> tuple[Optional[str], bool, Optional[str]]:
        if not model_key:
            return None, False, fallback_note
        if _image_model_key_blocked(model_key, task=task):
            return None, False, "運用ポリシーにより無効（動画内画像では Gemini 3 は使用禁止）"
        if model_key not in known_keys:
            return None, False, f"未登録モデル: {model_key}"
        return model_key, True, None

    def _slot_options(task: str) -> List[ImageModelCatalogOption]:
        slots = slots_conf.get("slots") if isinstance(slots_conf, dict) else None
        if not isinstance(slots, dict):
            return []
        out: List[ImageModelCatalogOption] = []
        for code in sorted((str(k) for k in slots.keys()), key=lambda s: s):
            resolved = _resolve_image_model_slot_selector(code, task=task, slots_conf=slots_conf)
            if resolved is None:
                continue
            resolved_key, meta = resolved
            enabled = True
            note_parts: List[str] = []

            desc = meta.get("slot_description")
            if isinstance(desc, str) and desc.strip():
                note_parts.append(desc.strip())

            if _image_model_key_blocked(resolved_key, task=task):
                enabled = False
                note_parts.append("運用ポリシーにより無効（動画内画像では Gemini 3 は使用禁止）")
            elif resolved_key not in known_keys:
                enabled = False
                note_parts.append(f"未登録モデル: {resolved_key}")
            else:
                m = model_index.get(resolved_key) or {}
                provider = str(m.get("provider") or "").strip()
                model_name = str(m.get("model_name") or "").strip()
                if provider and model_name:
                    note_parts.append(f"→ {resolved_key} ({provider} / {model_name})")
                else:
                    note_parts.append(f"→ {resolved_key}")

            label_hint = meta.get("slot_label")
            if not isinstance(label_hint, str) or not label_hint.strip():
                label_hint = resolved_key

            out.append(
                _opt(
                    id=f"0_slots:{code}",
                    label=f"0_slots · {code} · {label_hint}",
                    provider_group="0_slots",
                    variant="slot",
                    model_key=code,
                    enabled=enabled,
                    note=" / ".join([p for p in note_parts if p]) or None,
                )
            )
        return out

    # Curated options (requested by user):
    fw_schnell, fw_schnell_ok, fw_schnell_note = _mk_opt(
        "fireworks_flux_1_schnell_fp8", fallback_note="未設定", task="thumbnail_image_gen"
    )
    fw_pro, fw_pro_ok, fw_pro_note = _mk_opt(
        "fireworks_flux_kontext_pro", fallback_note="未設定", task="thumbnail_image_gen"
    )
    fw_max, fw_max_ok, fw_max_note = _mk_opt(
        "fireworks_flux_kontext_max", fallback_note="未設定", task="thumbnail_image_gen"
    )

    g_flash, g_flash_ok, g_flash_note = _mk_opt(
        "gemini_2_5_flash_image", fallback_note="未設定", task="thumbnail_image_gen"
    )
    # Gemini 3 is allowed for thumbnails, but disabled for video images.
    g_three_thumb, g_three_thumb_ok, g_three_thumb_note = _mk_opt(
        "gemini_3_pro_image_preview", fallback_note="未設定", task="thumbnail_image_gen"
    )
    g_three_video, g_three_video_ok, g_three_video_note = _mk_opt(
        "gemini_3_pro_image_preview", fallback_note="未設定", task="visual_image_gen"
    )

    or_flash, or_flash_ok, or_flash_note = _mk_opt(
        "openrouter_gemini_2_5_flash_image", fallback_note="未設定", task="thumbnail_image_gen"
    )
    # OpenRouter Gemini 3 image preview is not configured in normal ops.
    or_three, or_three_ok, or_three_note = (None, False, "未設定: OpenRouter Gemini 3 は運用で使いません")

    # fal.ai is planned but not configured yet.
    fal_note = "未対応: fal.ai はこれから拡張予定"

    thumbnail_opts = [
        _opt(
            id="1_fireworks:flux_schnell",
            label="1_fireworks · FLUX schnell",
            provider_group="1_fireworks",
            variant="schnell",
            model_key=fw_schnell,
            enabled=fw_schnell_ok,
            note=fw_schnell_note,
        ),
        _opt(
            id="1_fireworks:flux_pro",
            label="1_fireworks · FLUX pro",
            provider_group="1_fireworks",
            variant="pro",
            model_key=fw_pro,
            enabled=fw_pro_ok,
            note=fw_pro_note,
        ),
        _opt(
            id="1_fireworks:flux_max",
            label="1_fireworks · FLUX max",
            provider_group="1_fireworks",
            variant="max",
            model_key=fw_max,
            enabled=fw_max_ok,
            note=fw_max_note,
        ),
        _opt(
            id="2_google:gemini_2_5_flash_image",
            label="2_google · Gemini 2.5 Flash Image",
            provider_group="2_google",
            variant="gemini_2_5_flash_image",
            model_key=g_flash,
            enabled=g_flash_ok,
            note=g_flash_note,
        ),
        _opt(
            id="2_google:gemini_3_pro_image",
            label="2_google · Gemini 3 Pro Image",
            provider_group="2_google",
            variant="gemini_3_pro_image",
            model_key=g_three_thumb,
            enabled=g_three_thumb_ok,
            note=g_three_thumb_note,
        ),
        _opt(
            id="3_fal.ai:flux_schnell",
            label="3_fal.ai · FLUX schnell (coming soon)",
            provider_group="3_fal.ai",
            variant="schnell",
            model_key=None,
            enabled=False,
            note=fal_note,
        ),
        _opt(
            id="3_fal.ai:flux_pro",
            label="3_fal.ai · FLUX pro (coming soon)",
            provider_group="3_fal.ai",
            variant="pro",
            model_key=None,
            enabled=False,
            note=fal_note,
        ),
        _opt(
            id="3_fal.ai:flux_max",
            label="3_fal.ai · FLUX max (coming soon)",
            provider_group="3_fal.ai",
            variant="max",
            model_key=None,
            enabled=False,
            note=fal_note,
        ),
        _opt(
            id="4_openrouter:gemini_2_5_flash_image",
            label="4_openrouter · Gemini 2.5 Flash Image",
            provider_group="4_openrouter",
            variant="gemini_2_5_flash_image",
            model_key=or_flash,
            enabled=or_flash_ok,
            note=or_flash_note,
        ),
        _opt(
            id="4_openrouter:gemini_3_pro_image",
            label="4_openrouter · Gemini 3 Pro Image (disabled)",
            provider_group="4_openrouter",
            variant="gemini_3_pro_image",
            model_key=or_three,
            enabled=or_three_ok,
            note=or_three_note,
        ),
    ]

    video_opts = [
        *[opt for opt in thumbnail_opts if opt.id != "2_google:gemini_3_pro_image"],
        _opt(
            id="2_google:gemini_3_pro_image",
            label="2_google · Gemini 3 Pro Image (disabled for video images)",
            provider_group="2_google",
            variant="gemini_3_pro_image",
            model_key=g_three_video,
            enabled=g_three_video_ok,
            note=g_three_video_note,
        ),
    ]

    # Video-image opts are the same catalog (the engine differs; selection is per-channel).
    slot_thumbnail = _slot_options("thumbnail_image_gen")
    slot_video = _slot_options("visual_image_gen")
    return ImageModelRoutingCatalog(
        thumbnail=slot_thumbnail + thumbnail_opts,
        video_image=slot_video + video_opts,
    )


def _validate_image_model_key_for_routing(
    model_key: str,
    *,
    model_index: Dict[str, Dict[str, str]],
    slots_conf: Dict[str, Any],
    allow_empty: bool,
    label: str,
    task: str,
) -> Optional[str]:
    mk = str(model_key or "").strip()
    if not mk:
        return "" if allow_empty else None
    if mk in IMAGE_MODEL_KEY_BLOCKLIST:
        raise HTTPException(status_code=400, detail=f"{label}: blocked model_key: {mk}")
    if model_index and mk in model_index:
        return mk

    resolved = _resolve_image_model_slot_selector(mk, task=str(task), slots_conf=slots_conf)
    if resolved is not None:
        resolved_key, _meta = resolved
        if resolved_key in IMAGE_MODEL_KEY_BLOCKLIST:
            raise HTTPException(
                status_code=400,
                detail=f"{label}: blocked resolved model_key: {resolved_key}",
            )
        if model_index and resolved_key not in model_index:
            raise HTTPException(
                status_code=400,
                detail=f"{label}: slot resolves to unknown model_key: {resolved_key}",
            )
        return mk

    if model_index and mk not in model_index:
        raise HTTPException(status_code=400, detail=f"{label}: unknown model_key: {mk}")
    return mk


def get_image_model_routing():
    model_index = _load_image_models_index_simple()
    slots_conf = _load_image_model_slots_config()
    models = [
        ImageModelKeyInfo(key=k, provider=v["provider"], model_name=v["model_name"])
        for k, v in sorted(model_index.items(), key=lambda kv: str(kv[0]))
    ]
    catalog = _build_image_model_routing_catalog(model_index, slots_conf=slots_conf)

    with THUMBNAIL_TEMPLATES_LOCK:
        _, templates_doc = _load_thumbnail_templates_document()
    with VIDEO_CHANNEL_PRESETS_LOCK:
        _, channel_presets_doc = _load_video_channel_presets_document()

    channels: List[ChannelImageModelRouting] = []
    for ch in _list_planning_channel_codes():
        thumb = _resolve_thumbnail_model_selection(
            ch,
            templates_doc=templates_doc,
            model_index=model_index,
            slots_conf=slots_conf,
        )
        vid = _resolve_video_image_model_selection(
            ch,
            channel_presets_doc=channel_presets_doc,
            model_index=model_index,
            slots_conf=slots_conf,
        )
        channels.append(ChannelImageModelRouting(channel=ch, thumbnail=thumb, video_image=vid))

    return ImageModelRoutingResponse(
        generated_at=_utc_now_iso_z(),
        blocked_model_keys=sorted(list(IMAGE_MODEL_KEY_BLOCKLIST)),
        models=models,
        catalog=catalog,
        channels=channels,
    )


def patch_image_model_routing(channel: str, payload: ImageModelRoutingUpdate):
    channel_code = normalize_channel_code(channel)
    model_index = _load_image_models_index_simple()
    slots_conf = _load_image_model_slots_config()

    if payload.thumbnail_model_key is not None:
        mk = _validate_image_model_key_for_routing(
            payload.thumbnail_model_key,
            model_index=model_index,
            slots_conf=slots_conf,
            allow_empty=False,
            label="thumbnail_model_key",
            task="thumbnail_image_gen",
        )
        if mk is None or not mk:
            raise HTTPException(status_code=400, detail="thumbnail_model_key is required")
        now = datetime.now(timezone.utc).isoformat()
        with THUMBNAIL_TEMPLATES_LOCK:
            path, doc = _load_thumbnail_templates_document()
            channels = doc.get("channels")
            if not isinstance(channels, dict):
                channels = {}
                doc["channels"] = channels
            ch_doc = channels.get(channel_code)
            if not isinstance(ch_doc, dict):
                ch_doc = {"default_template_id": None, "templates": []}
                channels[channel_code] = ch_doc
            templates = ch_doc.get("templates")
            if not isinstance(templates, list):
                templates = []
                ch_doc["templates"] = templates
            default_id = str(ch_doc.get("default_template_id") or "").strip() or None

            chosen: Optional[dict] = None
            if default_id:
                for tpl in templates:
                    if isinstance(tpl, dict) and str(tpl.get("id") or "").strip() == default_id:
                        chosen = tpl
                        break
            if chosen is None and templates:
                chosen = next((tpl for tpl in templates if isinstance(tpl, dict)), None)
                if chosen is not None and not default_id:
                    default_id = str(chosen.get("id") or "").strip() or None
                    if default_id:
                        ch_doc["default_template_id"] = default_id

            if chosen is None:
                template_id = f"{channel_code.lower()}_default_v1"
                chosen = {
                    "id": template_id,
                    "name": f"{channel_code} default",
                    "image_model_key": mk,
                    "prompt_template": "",
                    "created_at": now,
                    "updated_at": now,
                }
                templates.append(chosen)
                ch_doc["default_template_id"] = template_id
            else:
                chosen.setdefault("created_at", now)
                chosen["updated_at"] = now
                chosen["image_model_key"] = mk
            _write_thumbnail_templates_document(path, doc)

    if payload.video_image_model_key is not None:
        mk = _validate_image_model_key_for_routing(
            payload.video_image_model_key,
            model_index=model_index,
            slots_conf=slots_conf,
            allow_empty=True,
            label="video_image_model_key",
            task="visual_image_gen",
        )
        with VIDEO_CHANNEL_PRESETS_LOCK:
            path, doc = _load_video_channel_presets_document()
            channels = doc.get("channels")
            if not isinstance(channels, dict):
                channels = {}
                doc["channels"] = channels
            entry = channels.get(channel_code)
            if not isinstance(entry, dict):
                entry = {"name": channel_code}
                channels[channel_code] = entry
            image_generation = entry.get("image_generation")
            if not isinstance(image_generation, dict):
                image_generation = {}
                entry["image_generation"] = image_generation
            if mk:
                image_generation["model_key"] = mk
            else:
                image_generation.pop("model_key", None)
            _write_video_channel_presets_document(path, doc)

    with THUMBNAIL_TEMPLATES_LOCK:
        _, templates_doc = _load_thumbnail_templates_document()
    with VIDEO_CHANNEL_PRESETS_LOCK:
        _, channel_presets_doc = _load_video_channel_presets_document()

    thumb = _resolve_thumbnail_model_selection(
        channel_code,
        templates_doc=templates_doc,
        model_index=model_index,
        slots_conf=slots_conf,
    )
    vid = _resolve_video_image_model_selection(
        channel_code,
        channel_presets_doc=channel_presets_doc,
        model_index=model_index,
        slots_conf=slots_conf,
    )
    return ChannelImageModelRouting(channel=channel_code, thumbnail=thumb, video_image=vid)

