from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from fastapi import HTTPException

from backend.app.channel_info_store import CHANNELS_DIR
from backend.app.channels_models import ChannelBenchmarksSpec, ChannelProfileResponse, _resolve_video_workflow
from backend.app.codex_settings_store import _resolve_channel_chapter_count, _resolve_channel_target_chars
from backend.core.tools.channel_profile import load_channel_profile
from factory_common.paths import repo_root as ssot_repo_root
from factory_common.paths import script_pkg_root
from script_pipeline.tools import planning_requirements

logger = logging.getLogger(__name__)

PROJECT_ROOT = ssot_repo_root()
AUDIO_CHANNELS_DIR = script_pkg_root() / "audio" / "channels"


def _resolve_channel_dir(channel_code: str) -> Path:
    upper = channel_code.upper()
    direct = CHANNELS_DIR / upper
    if direct.is_dir() and (direct / "channel_info.json").exists():
        return direct
    prefix = f"{upper}-"
    for entry in CHANNELS_DIR.iterdir():
        if entry.is_dir() and entry.name.upper().startswith(prefix):
            if (entry / "channel_info.json").exists():
                return entry
    raise HTTPException(status_code=404, detail=f"channel_info.json が見つかりません: {channel_code}")


def _load_channel_info_payload(channel_code: str) -> tuple[Path, Dict[str, Any], Path]:
    channel_dir = _resolve_channel_dir(channel_code)
    info_path = channel_dir / "channel_info.json"
    try:
        payload = json.loads(info_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"channel_info.json の解析に失敗しました: {exc}") from exc
    return info_path, payload, channel_dir


def _load_voice_config_payload(channel_code: str, *, required: bool = False) -> tuple[Optional[Path], Dict[str, Any]]:
    config_path = AUDIO_CHANNELS_DIR / channel_code.upper() / "voice_config.json"
    if not config_path.exists():
        if required:
            raise HTTPException(status_code=404, detail=f"voice_config.json が見つかりません: {config_path}")
        return None, {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"voice_config.json の解析に失敗しました: {exc}") from exc
    return config_path, payload


def _build_channel_profile_response(channel_code: str) -> ChannelProfileResponse:
    try:
        profile = load_channel_profile(channel_code)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    info_path, info_payload, _ = _load_channel_info_payload(channel_code)
    _ = info_path  # suppress unused warning
    _, voice_payload = _load_voice_config_payload(channel_code, required=False)
    youtube_info = info_payload.get("youtube") or {}
    default_tags = info_payload.get("default_tags") or None
    audio_rules = voice_payload.get("section_voice_rules") or {}
    planning_persona = planning_requirements.get_channel_persona(channel_code)
    planning_persona_path = planning_requirements.get_persona_doc_path(channel_code)
    planning_required = planning_requirements.get_channel_requirement_specs(channel_code)
    planning_defaults = planning_requirements.get_description_defaults(channel_code)
    template_info = planning_requirements.get_planning_template_info(channel_code)
    planning_template_path = template_info.get("path")
    planning_template_headers = template_info.get("headers") or []
    planning_template_sample = template_info.get("sample") or []
    youtube_title = youtube_info.get("title") or info_payload.get("youtube_title")
    youtube_description = info_payload.get("youtube_description") or youtube_info.get("description")
    youtube_handle = youtube_info.get("handle") or info_payload.get("youtube_handle")
    benchmarks: Optional[ChannelBenchmarksSpec] = None
    raw_benchmarks = info_payload.get("benchmarks")
    if isinstance(raw_benchmarks, dict):
        try:
            benchmarks = ChannelBenchmarksSpec.model_validate(raw_benchmarks)
        except Exception:
            benchmarks = None

    chars_min, chars_max = _resolve_channel_target_chars(channel_code)
    chapter_count = _resolve_channel_chapter_count(channel_code)

    # Default model routing for batch/script generation is controlled by numeric slots (LLM_MODEL_SLOT).
    # Keep this in the channel profile response so the UI can prefill without guessing.
    llm_slot: int = 0
    try:
        slots_path = PROJECT_ROOT / "configs" / "llm_model_slots.yaml"
        if slots_path.exists():
            doc = yaml.safe_load(slots_path.read_text(encoding="utf-8")) or {}
            if isinstance(doc, dict):
                raw = doc.get("default_slot")
                if raw is not None and str(raw).strip() != "":
                    llm_slot = max(0, int(str(raw).strip()))
    except Exception:
        llm_slot = 0

    return ChannelProfileResponse(
        channel_code=profile.code,
        channel_name=profile.name,
        audience_profile=profile.audience_profile,
        persona_summary=profile.persona_summary,
        script_prompt=profile.script_prompt or None,
        description=info_payload.get("description"),
        default_tags=default_tags,
        youtube_title=youtube_title,
        youtube_description=youtube_description,
        youtube_handle=youtube_handle or youtube_info.get("custom_url"),
        video_workflow=_resolve_video_workflow(info_payload),
        benchmarks=benchmarks,
        audio_default_voice_key=voice_payload.get("default_voice_key"),
        audio_section_voice_rules=audio_rules if isinstance(audio_rules, dict) else {},
        default_min_characters=chars_min,
        default_max_characters=chars_max,
        chapter_count=chapter_count,
        llm_slot=llm_slot,
        llm_model=str(llm_slot),
        planning_persona=planning_persona or profile.persona_summary or profile.audience_profile,
        planning_persona_path=planning_persona_path,
        planning_required_fieldsets=planning_required,
        planning_description_defaults=planning_defaults,
        planning_template_path=planning_template_path,
        planning_template_headers=planning_template_headers,
        planning_template_sample=planning_template_sample,
    )

