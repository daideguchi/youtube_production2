"""
FastAPI backend for the React UI.

This file is intentionally the canonical entrypoint (`backend.main:app`) referenced by SSOT docs.
It is large; use the section map below to avoid getting lost.

SECTION MAP (grep for these tokens)
- Settings/UI keys: `/api/settings/` / `_get_ui_settings`
- Model routing (SSOT): `configs/llm_router.yaml` / `LLM_MODEL_SLOT` / `/model-policy` (frontend)
- Prompts: `/api/prompts`
- Channels: `/api/channels` / `ChannelProfileResponse`
- Planning CSV: `/api/planning`
- SSOT docs (persona/templates): `/api/ssot/`
- Dashboard: `/api/dashboard/overview`
- Batch script generation: `/api/batch-workflow/`
- Audio/TTS tools: `/api/channels/{channel}/videos/{video}/tts/`
- Video assets: `/api/workspaces/video/`

Routers implemented under `apps/ui-backend/backend/routers/` are included near the `include_router` block.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import os
import copy
import subprocess
import sys
import uuid
import re
import urllib.request
import urllib.parse
import difflib
import hashlib
import mimetypes
import shutil
import zipfile
import base64
import unicodedata
import requests
import yaml
from PIL import Image, ImageStat
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Literal, Sequence
from collections import deque
from enum import Enum
import sqlite3
import threading
import time

import logging

# NOTE: Do not mutate sys.path here.
# Supported entrypoints (e.g. `scripts/start_all.sh`, `apps/ui-backend/tools/start_manager.py`)
# set a deterministic PYTHONPATH and run uvicorn from `apps/ui-backend/` so imports resolve
# without per-module bootstrapping.

from fastapi.staticfiles import StaticFiles
# audio_tts routing helpers
from audio_tts.tts.routing import (
    load_routing_config,
    resolve_eleven_model,
    resolve_eleven_voice,
    resolve_voicevox_speaker_id,
)

from backend.core.portalocker_compat import portalocker
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form, Body, BackgroundTasks
from backend.routers import jobs
from fastapi import APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.tools.optional_fields_registry import (
    OPTIONAL_FIELDS,
    FIELD_KEYS,
    get_planning_section,
    update_planning_from_row,
)
from backend.audio import pause_tags, wav_tools
from backend.core.tools.content_processor import ContentProcessor
from backend.core.tools.audio_manager import AudioManager
from backend.core.tools.channel_profile import load_channel_profile
from backend.core.tools.channel_stats_store import merge_channel_stats_into_channel_info, write_channel_stats
from backend.core.tools.prompt_utils import auto_placeholder_values
from backend.core.tools import thumbnails_lookup as thumbnails_lookup_tools
# 移行先: script_pipeline/tools 配下の簡易実装を利用
from script_pipeline.tools import planning_requirements, planning_store
from backend.app.channel_info_store import (
    CHANNELS_DIR,
    CHANNEL_INFO_PATH,
    find_channel_directory,
    infer_channel_genre,
    rebuild_channel_catalog,
    refresh_channel_info,
    resolve_channel_title,
)
from backend.app.channel_catalog import (
    list_channel_dirs,
    list_known_channel_codes,
    list_planning_video_numbers,
    list_video_dirs,
)
from backend.app.channels_models import (
    BenchmarkChannelSpec,
    BenchmarkScriptSampleSpec,
    ChannelAuditItemResponse,
    ChannelBenchmarksSpec,
    ChannelBranding,
    ChannelProfileResponse,
    ChannelProfileUpdateAudio,
    ChannelProfileUpdateRequest,
    ChannelRegisterRequest,
    ChannelSummaryResponse,
    PersonaDocumentResponse,
    PersonaDocumentUpdateRequest,
    PlanningRequirementSummary,
    PlanningTemplateResponse,
    PlanningTemplateUpdateRequest,
    VideoWorkflowSpec,
    _resolve_video_workflow,
)
from backend.app.settings_models import (
    CodexCliConfig,
    CodexCliProfile,
    CodexExecConfig,
    CodexSettingsResponse,
    CodexSettingsUpdate,
    LLMConfig,
    LLMSettingsResponse,
    LLMSettingsUpdate,
)
from backend.app.scripts_models import (
    HumanScriptResponse,
    HumanScriptUpdateRequest,
    NaturalCommandAction,
    NaturalCommandRequest,
    NaturalCommandResponse,
    OptimisticUpdateRequest,
    ScriptTextResponse,
    TextUpdateRequest,
)
from backend.app.status_models import (
    MAX_STATUS_LENGTH,
    STAGE_ORDER,
    VALID_STAGE_STATUSES,
    ReadyUpdateRequest,
    StageStatus,
    StageUpdateRequest,
    StatusUpdateRequest,
    ensure_expected_updated_at,
)
from backend.app.status_store import (
    PROGRESS_STATUS_PATH,
    default_status_payload as _default_status_payload,
    load_or_init_status,
    save_status as _save_status_impl,
)
from backend.app.image_model_routing_policy import IMAGE_MODEL_KEY_BLOCKLIST, _image_model_key_blocked
from backend.app.image_model_routing_models import (
    IMAGE_MODEL_ROUTING_SCHEMA_V1,
    ChannelImageModelRouting,
    ImageModelCatalogOption,
    ImageModelKeyInfo,
    ImageModelRoutingCatalog,
    ImageModelRoutingResponse,
    ImageModelRoutingSelection,
    ImageModelRoutingUpdate,
)
from backend.app.image_model_routing_handlers import get_image_model_routing, patch_image_model_routing
from backend.app.planning_models import (
    PlanningCreateRequest,
    PlanningCsvRowResponse,
    PlanningFieldPayload,
    PlanningInfoResponse,
    PlanningProgressUpdateRequest,
    PlanningSpreadsheetResponse,
    PlanningUpdateRequest,
    PlanningUpdateResponse,
)
from backend.app.video_registry_models import (
    ArtifactEntryResponse,
    ArtifactsSummaryResponse,
    VideoCreateRequest,
    VideoDetailResponse,
    VideoFileReferences,
    VideoGenerationInfo,
    VideoSummaryResponse,
)
from backend.app.planning_payload import build_planning_payload, build_planning_payload_from_row
from backend.app.datetime_utils import current_timestamp, current_timestamp_compact, parse_iso_datetime
from backend.app.script_text_utils import (
    _character_count_from_a_text,
    _fallback_character_count_from_files,
    _resolve_a_text_display_path,
)
from backend.app.status_payload_builder import build_status_payload, initialize_stage_payload
from backend.app.ssot_sync_guard import run_ssot_sync_for_channel
from backend.app.codex_settings_store import (
    _deep_merge_dict,
    _resolve_channel_chapter_count,
    _resolve_channel_target_chars,
    get_codex_settings,
    update_codex_settings,
)
from backend.app.lock_store import (
    LOCK_ALERT_CONFIG,
    LOCK_ALERT_CONFIG_PATH,
    LOCK_ALERT_STATE,
    LOCK_DB_PATH,
    LOCK_HISTORY,
    LOCK_METRICS,
    LOCK_TIMEOUT_SECONDS,
    emit_lock_alert,
    init_lock_storage,
    load_lock_alert_config,
    load_lock_history,
    maybe_emit_lock_alert,
    record_lock_event,
    reset_lock_alert_state,
    write_text_with_lock,
)
from backend.app.json_store import load_json, write_json
from backend.app.youtube_client import YouTubeDataClient, YouTubeDataAPIError
from backend.app.youtube_description_builder import _build_youtube_description
from backend.app.youtube_uploads_store import (
    YOUTUBE_BRANDING_BACKOFF,
    YOUTUBE_BRANDING_TTL,
    YOUTUBE_CLIENT,
    YOUTUBE_UPLOAD_BACKOFF,
    YOUTUBE_UPLOAD_CACHE,
    YOUTUBE_UPLOAD_CACHE_DIR,
    YOUTUBE_UPLOAD_CACHE_TTL,
    YOUTUBE_UPLOAD_FAILURE_STATE,
    YOUTUBE_UPLOADS_MAX_REFRESH_PER_REQUEST,
    _load_cached_uploads,
    _save_cached_uploads,
)
from backend.app.video_channel_presets_store import (
    VIDEO_CHANNEL_PRESETS_LOCK,
    _load_video_channel_presets_document,
    _write_video_channel_presets_document,
)
from backend.app.redo_models import RedoUpdateRequest, RedoUpdateResponse
from backend.app.thumbnails_constants import (
    THUMBNAIL_LIBRARY_MAX_BYTES,
    THUMBNAIL_PROJECT_STATUSES,
    THUMBNAIL_REMOTE_FETCH_TIMEOUT,
    THUMBNAIL_SUPPORTED_EXTENSIONS,
)
from backend.app.thumbnails_disk_variants import _collect_disk_thumbnail_variants, _coerce_video_from_dir
from backend.app.thumbnails_projects_store import (
    THUMBNAIL_PROJECTS_LOCK,
    _load_thumbnail_projects_document,
    _write_thumbnail_projects_document,
)
from backend.app.thumbnails_templates_store import (
    THUMBNAIL_TEMPLATES_LOCK,
    _load_thumbnail_templates_document,
    _write_thumbnail_templates_document,
)
from backend.app.thumbnails_library_models import (
    ThumbnailLibraryAssetResponse,
    ThumbnailLibraryAssignRequest,
    ThumbnailLibraryAssignResponse,
    ThumbnailLibraryImportRequest,
    ThumbnailLibraryRenameRequest,
    ThumbnailQuickHistoryEntry,
)
from backend.app.thumbnails_models import ThumbnailOverrideRequest, ThumbnailOverrideResponse
from backend.app.thumbnails_project_models import ThumbnailProjectResponse
from backend.app.thumbnails_overview_models import (
    ThumbnailChannelBlockResponse,
    ThumbnailChannelSummaryResponse,
    ThumbnailChannelVideoResponse,
    ThumbnailOverviewResponse,
)
from backend.app.thumbnails_variant_models import ThumbnailVariantResponse
from backend.app.srt_models import SRTIssue, SRTVerifyResponse
from backend.app.srt_verify import verify_srt_file
from backend.app.audio_metadata_utils import normalize_audio_metadata, normalize_audio_path_string
from backend.app.stage_status_utils import _normalize_status_token, _stage_status_value
from backend.app.video_effective_status import _derive_effective_stages, _derive_effective_video_status
from backend.app.tts_content_analyzer import analyze_tts_content
from backend.app.tts_tagged_text import _compose_tagged_tts, _parse_tagged_tts
from backend.app.video_progress_models import ThumbnailProgressResponse, VideoImagesProgressResponse
from backend.app.normalize import (
    normalize_channel_code,
    normalize_optional_text,
    normalize_planning_video_number,
    normalize_video_number,
)
from backend.app.path_utils import safe_relative_path
from backend.app.episode_store import (
    _detect_artifact_path,
    get_audio_duration_seconds,
    load_status,
    load_status_optional,
    resolve_audio_path,
    resolve_log_path,
    resolve_srt_path,
    resolve_text_file,
    video_base_dir,
)
from backend.app.ui_settings_store import (
    OPENROUTER_API_KEY,
    _get_effective_openai_key,
    _get_effective_openrouter_key,
    _get_ui_settings,
    _load_env_value,
    _normalize_llm_settings,
    _validate_provider_endpoint,
    _write_ui_settings,
)
from backend.app.llm_catalog_store import (
    _fetch_openrouter_generation,
    _get_openrouter_pricing_by_model_id,
    _list_openai_model_ids,
    _list_openrouter_model_ids,
)
from backend.app.llm_settings_handlers import _build_llm_settings_response, get_llm_settings, update_llm_settings
from backend.video_production import video_router
from backend.routers import swap
from backend.routers import params
from backend.routers import kb
from backend.routers import channel_registry
from backend.routers import reading_dict
from backend.routers import audio_check
from backend.routers import audio_reports
from backend.routers import health
from backend.routers import healthz
from backend.routers import lock_metrics
from backend.routers import batch_tts
from backend.routers import batch_workflow
from backend.routers import episode_files
from factory_common.publish_lock import (
    is_episode_published_locked,
    mark_episode_published_locked,
    unmark_episode_published_locked,
)
from factory_common.alignment import (
    iter_thumbnail_catches_from_row,
    planning_hash_from_row,
    sha1_file as sha1_file_bytes,
)
from factory_common.paths import (
    assets_root as ssot_assets_root,
    audio_artifacts_root,
    audio_final_dir,
    audio_pkg_root,
    logs_root as ssot_logs_root,
    persona_path as ssot_persona_path,
    planning_root as ssot_planning_root,
    repo_root as ssot_repo_root,
    research_root as ssot_research_root,
    script_data_root as ssot_script_data_root,
    script_pkg_root,
    thumbnails_root as ssot_thumbnails_root,
    video_input_root as ssot_video_input_root,
    video_runs_root as ssot_video_runs_root,
)
from factory_common.youtube_handle import (
    YouTubeHandleResolutionError,
    normalize_youtube_handle,
    resolve_youtube_channel_id_from_handle,
)

_llm_usage_import_error: Exception | None = None
try:
    from backend.routers import llm_usage
except Exception as e:  # pragma: no cover - optional router
    llm_usage = None  # type: ignore[assignment]
    _llm_usage_import_error = e

try:
    import google.generativeai as genai
except Exception:  # pragma: no cover - optional dependency
    genai = None

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency
    OpenAI = None

LOGGER_NAME = "ui_backend"
logger = logging.getLogger(LOGGER_NAME)

REPO_ROOT = ssot_repo_root()
# NOTE: PROJECT_ROOT is treated as repo-root throughout this file (legacy alias).
PROJECT_ROOT = REPO_ROOT
# 旧 commentary_01_srtfile_v2 から script_pipeline へ移行済み
SCRIPT_PIPELINE_ROOT = script_pkg_root()
DATA_ROOT = ssot_script_data_root()
EXPORTS_DIR = SCRIPT_PIPELINE_ROOT / "exports"
CHANNEL_PLANNING_DIR = ssot_planning_root() / "channels"
# Legacy single-file planning CSV override (kept for older tests/tooling).
PLANNING_CSV_PATH: Path | None = None
SPREADSHEET_EXPORT_DIR = EXPORTS_DIR / "spreadsheets"
THUMBNAIL_ASSETS_DIR = ssot_thumbnails_root() / "assets"
LOGS_ROOT = ssot_logs_root()


from backend.app.planning_csv_store import (  # noqa: E402
    _maybe_int_from_token,
    _normalize_video_number_token,
    _read_channel_csv_rows,
    _write_csv_with_lock,
)


AUDIO_CHANNELS_DIR = SCRIPT_PIPELINE_ROOT / "audio" / "channels"

CHANNEL_PROFILE_LOG_DIR = LOGS_ROOT / "regression"
THUMBNAIL_QUICK_HISTORY_PATH = LOGS_ROOT / "regression" / "thumbnail_quick_history.jsonl"
THUMBNAIL_QUICK_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)


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


def _sanitize_script_prompt(value: str) -> str:
    normalized = value.replace("\r\n", "\n").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="script_prompt を入力してください。")
    if "///" in normalized:
        raise HTTPException(status_code=400, detail="script_prompt に '///' は使用できません。")
    return normalized


def _clean_default_tags(values: Optional[List[str]]) -> Optional[List[str]]:
    if values is None:
        return None
    cleaned: List[str] = []
    for raw in values:
        if raw is None:
            continue
        tag = raw.strip()
        if not tag:
            continue
        if len(tag) > 64:
            raise HTTPException(status_code=400, detail=f"タグが長すぎます: {tag[:32]}…")
        cleaned.append(tag)
    if len(cleaned) > 50:
        raise HTTPException(status_code=400, detail="タグは最大50件までです。")
    return cleaned


def _normalize_youtube_handle_key(value: str) -> str:
    return normalize_youtube_handle(value).lower()


def _ensure_unique_youtube_handle(channel_code: str, handle: str, channel_info_map: Dict[str, dict]) -> None:
    """
    Ensure a YouTube handle maps to exactly one internal channel (accident prevention).
    """

    target = _normalize_youtube_handle_key(handle)
    conflicts: List[str] = []
    for code, info in (channel_info_map or {}).items():
        if code.upper() == channel_code.upper():
            continue
        youtube_info = info.get("youtube") or {}
        other = youtube_info.get("handle") or youtube_info.get("custom_url") or ""
        if not other:
            continue
        try:
            other_key = _normalize_youtube_handle_key(str(other))
        except Exception:
            continue
        if other_key == target:
            conflicts.append(code.upper())
    if conflicts:
        conflicts_s = ", ".join(sorted(set(conflicts)))
        raise HTTPException(
            status_code=400,
            detail=f"YouTubeハンドル {normalize_youtube_handle(handle)} が複数チャンネルに重複しています: {conflicts_s}",
        )


def _checksum_text(value: Optional[str]) -> str:
    text = value or ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_change(
    changes: List[Dict[str, Any]],
    field: str,
    old_value: Any,
    new_value: Any,
    *,
    redact: bool = False,
) -> None:
    if old_value == new_value:
        return
    if redact:
        entry = {
            "field": field,
            "old_len": len(old_value or ""),
            "new_len": len(new_value or ""),
            "old_checksum": _checksum_text(old_value),
            "new_checksum": _checksum_text(new_value),
        }
    else:
        entry = {"field": field, "old": old_value, "new": new_value}
    changes.append(entry)


def _append_channel_profile_log(channel_code: str, changes: List[Dict[str, Any]]) -> None:
    if not changes:
        return
    log_dir = CHANNEL_PROFILE_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"channel_profile_edit_{datetime.now(timezone.utc):%Y%m%d}.log"
    entry = {
        "timestamp": current_timestamp(),
        "channel_code": channel_code,
        "changes": changes,
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


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


def resolve_project_path(candidate: Optional[str]) -> Optional[Path]:
    """Resolve a repository-relative path safely."""
    if not candidate:
        return None
    path = Path(candidate)
    if not path.is_absolute():
        path = (PROJECT_ROOT / candidate).resolve()
    else:
        path = path.resolve()
    try:
        path.relative_to(PROJECT_ROOT)
    except ValueError:
        return None
    return path if path.exists() else None


def _find_commentary_input_asset(channel_code: str, video_number: str, suffix: str) -> Optional[Path]:
    """Locate WAV/SRT that were synced into workspaces/video/input (mirror)."""
    root = ssot_video_input_root()
    if not root.exists():
        return None
    pattern = f"**/{channel_code}-{video_number}.{suffix}"
    for match in sorted(root.glob(pattern)):
        if match.is_file():
            return match.resolve()
    return None


def save_status(channel_code: str, video_number: str, payload: dict) -> None:
    _save_status_impl(
        channel_code,
        video_number,
        payload,
        data_root=DATA_ROOT,
        progress_status_path=PROGRESS_STATUS_PATH,
    )


def _summarize_video_detail_artifacts(
    channel_code: str,
    video_number: str,
    *,
    base_dir: Path,
    content_dir: Path,
    audio_prep_dir: Path,
    assembled_path: Path,
    assembled_human_path: Path,
    b_text_with_pauses_path: Path,
    audio_path: Optional[Path],
    srt_path: Optional[Path],
) -> Dict[str, Any]:
    def _iso_mtime(mtime: float) -> str:
        return datetime.fromtimestamp(mtime, timezone.utc).isoformat().replace("+00:00", "Z")

    def _count_dir_children(path: Path, *, max_items: int = 10_000) -> Optional[int]:
        if not path.exists() or not path.is_dir():
            return None
        try:
            count = 0
            for _ in path.iterdir():
                count += 1
                if count >= max_items:
                    break
            return count
        except OSError:
            return None

    def _entry(
        *,
        key: str,
        label: str,
        path: Path,
        kind: Literal["file", "dir"] = "file",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        exists = False
        size_bytes = None
        modified_time = None
        try:
            exists = path.exists()
        except OSError:
            exists = False
        if exists:
            try:
                stat = path.stat()
                size_bytes = stat.st_size
                modified_time = _iso_mtime(stat.st_mtime)
            except OSError:
                pass
        return {
            "key": key,
            "label": label,
            "path": safe_relative_path(path) or str(path),
            "kind": kind,
            "exists": exists,
            "size_bytes": size_bytes,
            "modified_time": modified_time,
            "meta": meta,
        }

    project_dir_label = safe_relative_path(base_dir) or str(base_dir)

    items: List[Dict[str, Any]] = []
    items.append(_entry(key="status", label="status.json", path=base_dir / "status.json"))

    items.append(
        _entry(
            key="content_dir",
            label="content/",
            path=content_dir,
            kind="dir",
            meta={"count": _count_dir_children(content_dir)},
        )
    )
    items.append(_entry(key="assembled_human", label="assembled_human.md", path=assembled_human_path))
    items.append(_entry(key="assembled", label="assembled.md", path=assembled_path))

    items.append(
        _entry(
            key="audio_prep_dir",
            label="audio_prep/",
            path=audio_prep_dir,
            kind="dir",
            meta={"count": _count_dir_children(audio_prep_dir)},
        )
    )
    b_label = "TTS入力スナップショット (a_text.txt)" if b_text_with_pauses_path.name == "a_text.txt" else "b_text_with_pauses.txt"
    items.append(_entry(key="b_text_with_pauses", label=b_label, path=b_text_with_pauses_path))
    items.append(_entry(key="audio_prep_log", label="audio_prep/log.json", path=audio_prep_dir / "log.json"))

    final_dir = audio_final_dir(channel_code, video_number)
    items.append(
        _entry(
            key="audio_final_dir",
            label="audio_tts final/",
            path=final_dir,
            kind="dir",
            meta={"count": _count_dir_children(final_dir)},
        )
    )
    expected_wav = final_dir / f"{channel_code}-{video_number}.wav"
    expected_srt = final_dir / f"{channel_code}-{video_number}.srt"
    items.append(_entry(key="final_wav", label="final wav", path=audio_path or expected_wav))
    items.append(_entry(key="final_srt", label="final srt", path=srt_path or expected_srt))
    items.append(_entry(key="final_log", label="final log.json", path=final_dir / "log.json"))
    items.append(_entry(key="final_a_text", label="final a_text.txt", path=final_dir / "a_text.txt"))

    return {"project_dir": project_dir_label, "items": items}


def append_audio_history_entry(channel_code: str, video_number: str, entry: Dict[str, Any]) -> None:
    from backend.app.status_store import append_audio_history_entry as impl

    impl(channel_code, video_number, entry)


def _heuristic_natural_command(command: str, tts_content: str) -> Tuple[List[NaturalCommandAction], str]:
    command = command.strip()
    if not command:
        return [], "コマンドが空です。"

    pause_match = re.search(
        r"(?:\[(?P<tag>\d+(?:\.\d+)?)\s*(?:s|秒)?\])|(?P<num>\d+(?:\.\d+)?)\s*(?:秒|s|sec)\s*(?:待って|ポーズ|休止|間|入れて|追加)",
        command,
        re.IGNORECASE,
    )
    if pause_match:
        seconds_str = pause_match.group("tag") or pause_match.group("num")
        try:
            seconds_value = float(seconds_str)
        except (TypeError, ValueError):
            seconds_value = None
        if seconds_value and seconds_value > 0:
            return [
                NaturalCommandAction(
                    type="insert_pause",
                    pause_seconds=seconds_value,
                    pause_scope="line_end",
                )
            ], f"{seconds_value:.2f}秒のポーズタグを挿入します。"

    quote_patterns = [
        r"「([^」]+)」を「([^」]+)」に",
        r"『([^』]+)』を『([^』]+)』に",
        r'"([^"\\]+)"を"([^"\\]+)"に',
        r'“([^”]+)”を“([^”]+)”に',
    ]
    original = replacement = None
    for pattern in quote_patterns:
        match = re.search(pattern, command)
        if match:
            original, replacement = match.group(1), match.group(2)
            break

    if not original or not replacement:
        return [], "自動解釈できませんでした。"

    if original not in tts_content:
        return [], f"指定したテキスト「{original}」が音声用テキスト内に見つかりませんでした。"

    scope = "all" if re.search(r"全部|全て|すべて", command) else "first"
    target = "tts"
    if "字幕" in command:
        target = "srt"

    action = NaturalCommandAction(
        type="replace",
        target=target,
        original=original,
        replacement=replacement,
        scope=scope,
        update_assembled=target == "tts",
        regenerate_audio=True,
    )
    return [action], "テキストの置換を実行します。"


def _call_llm_for_command(command: str, tts_content: str) -> Tuple[List[NaturalCommandAction], str]:
    truncated_tts = tts_content[:4000]
    prompt = f"""
あなたは台本編集アシスタントです。以下の音声用テキストに対してユーザーの指示を構造化されたアクションとして返してください。

音声用テキスト(抜粋):\n```\n{truncated_tts}\n```\n
出力は以下のJSON形式のみで返してください。説明や日本語の文章は含めないでください。\n
{{
  "message": "ユーザーへの短い説明",
  "actions": [
    {{
      "type": "replace",
      "target": "tts" または "srt",
      "original": "変更前テキスト",
      "replacement": "変更後テキスト",
      "scope": "first" または "all",
      "update_assembled": true/false,
      "regenerate_audio": true/false
    }},
    {{
      "type": "insert_pause",
      "pause_seconds": 0.5,
      "pause_scope": "cursor" または "line_end" または "section_end"
    }}
  ]
}}

ルール:
1. replace アクションを返す場合、original に含める文字列は必ずテキスト内に存在するものにすること。
2. pause 指示がある場合は insert_pause アクションを使い、pause_seconds を秒単位の数値で設定すること。
3. scope は「全て」「全部」などがある場合のみ "all" とし、それ以外は "first"。
4. 「字幕」「SRT」などが含まれる場合は target="srt"、それ以外は target="tts"。
5. 生成するJSON以外の文字を出力しない。

ユーザーコマンド: {command}
"""
    try:
        from factory_common.llm_router import get_router
    except Exception as exc:  # pragma: no cover - optional dependency mismatch
        raise RuntimeError(f"LLMRouter is not available: {exc}") from exc

    router = get_router()
    result = router.call_with_raw(
        task="tts_natural_command",
        messages=[{"role": "user", "content": prompt}],
        response_format="json_object",
    )
    response_text = str(result.get("content") or "").strip()

    if response_text.startswith("```"):
        response_text = response_text.strip("`").strip()
    if response_text.lower().startswith("json"):
        response_text = response_text[4:].strip()
    for prefix in ("```json", "```JSON"):
        if response_text.startswith(prefix):
            response_text = response_text[len(prefix) :].strip()
    for marker in (
        "<|begin_of_text|>",
        "<|end_of_text|>",
        "<|start_header_id|assistant|end_header_id|>",
        "<|eot_id|>",
        "<｜begin▁of▁sentence｜>",
        "<｜end▁of▁sentence｜>",
    ):
        response_text = response_text.replace(marker, "")
    response_text = response_text.strip()

    payload = json.loads(response_text)
    if not isinstance(payload, dict):
        raise ValueError("LLM response is not a JSON object")

    actions_data = payload.get("actions", [])
    message = payload.get("message") or "LLMで指示を解釈しました。"
    actions: List[NaturalCommandAction] = []
    for action_data in actions_data:
        try:
            action = NaturalCommandAction(**action_data)
        except Exception as exc:  # pragma: no cover - validation fallback
            logger.warning("Invalid action from LLM: %s -- %s", action_data, exc)
            continue
        if action.original and action.original in tts_content:
            actions.append(action)
        else:
            logger.warning("LLM suggested original text not found: %s", action.original)
    return actions, message


def interpret_natural_command(command: str, tts_content: str) -> Tuple[List[NaturalCommandAction], str]:
    try:
        actions, message = _call_llm_for_command(command, tts_content)
        if actions:
            return actions, message
    except SystemExit as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Natural command LLM failed: %s", exc)

    # fallback to heuristic parser
    return _heuristic_natural_command(command, tts_content)


ESSENTIAL_BRANDING_KEYS = ("avatar_url", "subscriber_count", "view_count", "video_count")


def _has_essential_branding(info: dict) -> bool:
    branding = info.get("branding") or {}
    for key in ESSENTIAL_BRANDING_KEYS:
        if branding.get(key) in (None, ""):
            return False
    return True


def _ensure_youtube_metrics(channel_code: str, info: dict) -> dict:
    if YOUTUBE_CLIENT is None:
        return info
    info = merge_channel_stats_into_channel_info(channel_code, info)
    branding = info.get("branding") or {}
    youtube_payload = info.get("youtube") or {}
    synced_at = youtube_payload.get("synced_at") or branding.get("updated_at") or info.get("synced_at")
    needs_refresh = not _has_essential_branding(info) or YouTubeDataClient.is_stale(synced_at)
    if not needs_refresh:
        return info
    try:
        ensure_channel_branding(channel_code, info, force_refresh=True, ignore_backoff=False, strict=False)
    except HTTPException:
        return info
    return merge_channel_stats_into_channel_info(channel_code, info)


def ensure_channel_branding(
    channel_code: str,
    info: dict,
    *,
    force_refresh: bool = False,
    ignore_backoff: bool = False,
    strict: bool = False,
) -> Optional[dict]:
    if YOUTUBE_CLIENT is None:
        if strict:
            raise HTTPException(status_code=503, detail="YouTube Data API が無効化されています")
        return info.get("branding")

    if not force_refresh:
        return info.get("branding")

    refresh_channel_info()

    youtube_info = info.get("youtube") or {}
    identifier = (
        youtube_info.get("channel_id")
        or youtube_info.get("handle")
        or youtube_info.get("custom_url")
        or youtube_info.get("url")
        or youtube_info.get("source")
        or info.get("youtube_url")
        or info.get("youtube_handle")
        or (info.get("branding") or {}).get("handle")
        or (info.get("branding") or {}).get("custom_url")
    )

    if not identifier:
        if strict:
            raise HTTPException(status_code=400, detail="channel_info に YouTube チャンネルID/URL が登録されていません")
        return info.get("branding")

    branding = info.get("branding") or {}
    now = datetime.now(timezone.utc)
    backoff_until = YOUTUBE_BRANDING_BACKOFF.get(channel_code)
    if backoff_until and backoff_until > now and not ignore_backoff:
        if strict:
            raise HTTPException(
                status_code=429,
                detail=f"YouTube API は {backoff_until.isoformat()} まで一時停止中です",
            )
        return branding or None
    if backoff_until and (ignore_backoff or backoff_until <= now):
        YOUTUBE_BRANDING_BACKOFF.pop(channel_code, None)

    try:
        metadata = YOUTUBE_CLIENT.fetch_channel(identifier)
    except YouTubeDataAPIError as exc:  # pragma: no cover - API error logging only
        logger.warning("YouTube metadata fetch failed for %s: %s", channel_code, exc)
        message = str(exc).lower()
        if "quota" in message or "useratelimitexceeded" in message:
            YOUTUBE_BRANDING_BACKOFF[channel_code] = now + YOUTUBE_UPLOAD_BACKOFF
        if strict:
            raise HTTPException(status_code=502, detail=f"YouTube API error: {exc}") from exc
        return branding or None
    except Exception as exc:  # pragma: no cover - network failure handled gracefully
        logger.warning("Unexpected error during YouTube fetch for %s: %s", channel_code, exc)
        if strict:
            raise HTTPException(status_code=502, detail=f"Unexpected YouTube error: {exc}") from exc
        return branding or None

    branding_payload = metadata.to_branding_payload()
    if branding.get("theme_color"):
        branding_payload["theme_color"] = branding.get("theme_color")

    info["branding"] = branding_payload
    info.setdefault("youtube", {})
    youtube_payload = metadata.to_youtube_payload()
    youtube_payload["source"] = identifier
    now_iso = datetime.now(timezone.utc).isoformat()
    youtube_payload["synced_at"] = now_iso
    info["youtube"].update(youtube_payload)
    YOUTUBE_BRANDING_BACKOFF.pop(channel_code, None)

    write_channel_stats(
        channel_code,
        {
            "channel_id": channel_code.upper(),
            "synced_at": now_iso,
            "branding": branding_payload,
            "youtube": dict(info.get("youtube") or {}),
        },
    )
    return branding_payload


refresh_channel_info(force=True)
init_lock_storage()
CONTENT_PROCESSOR = ContentProcessor(PROJECT_ROOT)

def _build_fallback_thumbnail_project(channel_code: str, video_number: str) -> ThumbnailProjectResponse:
    script_id = f"{channel_code}-{video_number}"
    title = None
    sheet_title = None
    owner = None
    summary = None
    notes = None
    audio_stage = None
    script_stage = None
    try:
        status = load_status(channel_code, video_number)
    except HTTPException:
        status = None
    except Exception:
        status = None
    if status:
        script_id = status.get("script_id") or script_id
        metadata = status.get("metadata") or {}
        title = metadata.get("title") or metadata.get("video_title")
        sheet_title = metadata.get("sheet_title")
        owner = metadata.get("owner")
        summary = metadata.get("summary")
        notes = metadata.get("notes")
        stages = status.get("stages") or {}
        audio_stage = (stages.get("audio") or {}).get("status")
        script_stage = (stages.get("script") or {}).get("status")
    return ThumbnailProjectResponse(
        channel=channel_code,
        video=video_number,
        script_id=script_id,
        title=title,
        sheet_title=sheet_title,
        status="draft",
        owner=owner,
        summary=summary,
        notes=notes,
        tags=None,
        variants=[],
        ready_for_publish=False,
        updated_at=None,
        status_updated_at=None,
        due_at=None,
        selected_variant_id=None,
        audio_stage=audio_stage,
        script_stage=script_stage,
    )


def _variant_identity_keys(variant: ThumbnailVariantResponse) -> set[str]:
    keys: set[str] = set()
    if variant.id:
        keys.add(f"id::{variant.id.lower()}")
    if variant.image_url:
        keys.add(f"url::{variant.image_url.lower()}")
    if variant.image_path:
        keys.add(f"path::{variant.image_path.lower()}")
    return keys


def _max_iso_timestamp(a: Optional[str], b: Optional[str]) -> Optional[str]:
    if not a:
        return b
    if not b:
        return a
    a_dt = parse_iso_datetime(a)
    b_dt = parse_iso_datetime(b)
    if a_dt and b_dt:
        return a if a_dt >= b_dt else b
    if b_dt and not a_dt:
        return b
    return a


def _append_disk_variants(project: ThumbnailProjectResponse, disk_variants: List[ThumbnailVariantResponse]) -> None:
    if not disk_variants:
        return
    key_to_variant: Dict[str, ThumbnailVariantResponse] = {}
    for existing in project.variants:
        for key in _variant_identity_keys(existing):
            key_to_variant.setdefault(key, existing)

    for disk in disk_variants:
        identity_keys = _variant_identity_keys(disk)
        match: Optional[ThumbnailVariantResponse] = None
        for key in identity_keys:
            if key in key_to_variant:
                match = key_to_variant[key]
                break
        if match is not None:
            match.updated_at = _max_iso_timestamp(match.updated_at, disk.updated_at)
            if not match.created_at and disk.created_at:
                match.created_at = disk.created_at
            continue
        project.variants.append(disk)
        for key in identity_keys:
            key_to_variant.setdefault(key, disk)


def _merge_disk_thumbnail_variants(channel_code: str, entry: Dict[str, Any]) -> None:
    disk_map = _collect_disk_thumbnail_variants(channel_code)
    if not disk_map:
        return
    projects: List[ThumbnailProjectResponse] = entry.setdefault("projects", [])
    project_lookup = {project.video: project for project in projects}
    for video_number, variants in disk_map.items():
        project = project_lookup.get(video_number)
        if not project:
            project = _build_fallback_thumbnail_project(channel_code, video_number)
            projects.append(project)
            project_lookup[video_number] = project
        _append_disk_variants(project, variants)
    projects.sort(key=lambda project: (project.channel, project.video))


def _channel_primary_library_dir(channel_code: str, *, ensure: bool = False) -> Path:
    target_dir = THUMBNAIL_ASSETS_DIR / channel_code.upper() / "library"
    if ensure:
        target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def _channel_library_dirs(channel_code: str) -> List[Path]:
    channel_dir = THUMBNAIL_ASSETS_DIR / channel_code.upper()
    dirs: List[Path] = []
    primary = _channel_primary_library_dir(channel_code)
    if primary.exists():
        dirs.append(primary)
    if channel_dir.exists():
        for child in channel_dir.iterdir():
            if not child.is_dir():
                continue
            name = child.name
            if name.lower() == "library":
                continue
            dirs.append(child)
    return dirs


def _iter_library_files(channel_code: str):
    for directory in _channel_library_dirs(channel_code):
        try:
            entries = sorted(directory.rglob("*"), key=lambda path: path.as_posix().lower())
        except FileNotFoundError:
            continue
        for entry in entries:
            if entry.is_file():
                yield directory, entry


def _resolve_library_asset_path(channel_code: str, asset_identifier: str) -> tuple[Path, Path]:
    asset_identifier = asset_identifier.strip().lstrip("/").rstrip("/")
    if not asset_identifier:
        raise HTTPException(status_code=404, detail="invalid asset")
    rel_path = Path(asset_identifier)
    if rel_path.is_absolute() or any(part in {"", ".", ".."} for part in rel_path.parts):
        raise HTTPException(status_code=404, detail="invalid asset")
    channel_root = THUMBNAIL_ASSETS_DIR / channel_code.upper()
    candidate_dirs = _channel_library_dirs(channel_code)


    def _locate(candidate: Path) -> Optional[tuple[Path, Path]]:
        for base_dir in candidate_dirs:
            try:
                candidate.relative_to(base_dir)
            except ValueError:
                continue
            if candidate.is_file():
                return base_dir, candidate
        return None

    normalized = rel_path
    absolute_candidate = (channel_root / normalized).resolve()
    match = _locate(absolute_candidate)
    if match:
        return match

    for base_dir in candidate_dirs:
        candidate = (base_dir / normalized).resolve()
        try:
            candidate.relative_to(base_dir)
        except ValueError:
            continue
        if candidate.is_file():
            return base_dir, candidate
        # Avoid double-joining when the relative path already starts with the base directory name.
        parts = list(normalized.parts)
        def _norm_token(value: str) -> str:
            return unicodedata.normalize("NFC", value)

        if parts and _norm_token(parts[0]) == _norm_token(base_dir.name):
            inner = Path(*parts[1:])
            candidate2 = (base_dir / inner).resolve()
            try:
                candidate2.relative_to(base_dir)
            except ValueError:
                pass
            else:
                if candidate2.is_file():
                    return base_dir, candidate2

    if len(normalized.parts) == 1 and normalized.name:
        name = normalized.name
        for base_dir in candidate_dirs:
            candidate = base_dir / name
            if candidate.is_file():
                return base_dir, candidate

    raise HTTPException(status_code=404, detail="thumbnail asset not found")


def _build_library_asset_response(channel_code: str, file_path: Path, base_dir: Optional[Path] = None) -> ThumbnailLibraryAssetResponse:
    try:
        stat = file_path.stat()
    except OSError:
        raise HTTPException(status_code=404, detail="asset not found")
    channel_root = THUMBNAIL_ASSETS_DIR / channel_code.upper()
    base_dir = base_dir or file_path.parent
    try:
        base_dir_relative = base_dir.resolve().relative_to(channel_root.resolve())
        base_prefix = base_dir_relative.as_posix()
    except ValueError:
        base_prefix = base_dir.name
    try:
        inner_relative = file_path.relative_to(base_dir)
        inner_path = inner_relative.as_posix()
    except ValueError:
        inner_path = file_path.name
    relative_path = f"{base_prefix}/{inner_path}".strip("/")
    public_url = f"/thumbnails/library/{channel_code}/{relative_path}"
    timestamp = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    asset_id = hashlib.sha1(f"{channel_code}/library/{relative_path}".encode("utf-8")).hexdigest()[:12]
    return ThumbnailLibraryAssetResponse(
        id=asset_id,
        file_name=file_path.name,
        size_bytes=stat.st_size,
        updated_at=timestamp,
        public_url=public_url,
        relative_path=relative_path,
    )


def _list_channel_thumbnail_library(channel_code: str) -> List[ThumbnailLibraryAssetResponse]:
    assets: List[ThumbnailLibraryAssetResponse] = []
    for base_dir, entry in _iter_library_files(channel_code):
        if entry.suffix.lower() not in THUMBNAIL_SUPPORTED_EXTENSIONS:
            continue
        try:
            assets.append(_build_library_asset_response(channel_code, entry, base_dir=base_dir))
        except HTTPException:
            continue
    return assets


def _copy_library_asset_to_video(channel_code: str, video_number: str, source: Path) -> tuple[str, str]:
    if not source.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    dest_dir = THUMBNAIL_ASSETS_DIR / channel_code / video_number
    dest_dir.mkdir(parents=True, exist_ok=True)
    original_name = source.name
    stem = source.stem
    suffix = source.suffix or ""
    candidate_name = original_name
    counter = 1
    while (dest_dir / candidate_name).exists():
        candidate_name = f"{stem}_{counter:02d}{suffix}"
        counter += 1
    destination = dest_dir / candidate_name
    try:
        shutil.copy2(source, destination)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"ファイルのコピーに失敗しました: {exc}") from exc
    rel_path = f"{channel_code}/{video_number}/{candidate_name}"
    public_url = f"/thumbnails/assets/{rel_path}"
    return rel_path, public_url


def _append_thumbnail_quick_history(entry: dict) -> None:
    try:
        THUMBNAIL_QUICK_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with THUMBNAIL_QUICK_HISTORY_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:  # pragma: no cover - logging best effort
        logger.warning("Failed to record thumbnail quick history: %s", exc)


def _read_thumbnail_quick_history(channel_code: Optional[str], limit: int) -> List[ThumbnailQuickHistoryEntry]:
    if not THUMBNAIL_QUICK_HISTORY_PATH.exists():
        return []
    try:
        lines = THUMBNAIL_QUICK_HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as exc:  # pragma: no cover - logging best effort
        logger.warning("Failed to read thumbnail quick history: %s", exc)
        return []
    normalized_channel = channel_code.upper() if channel_code else None
    entries: List[ThumbnailQuickHistoryEntry] = []
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        channel_value = str(payload.get("channel") or "").upper()
        if normalized_channel and channel_value != normalized_channel:
            continue
        try:
            entries.append(ThumbnailQuickHistoryEntry.model_validate(payload))
        except Exception:
            continue
        if len(entries) >= limit:
            break
    return entries


def _color_name(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    if max(rgb) < 60:
        return "黒系"
    if min(rgb) > 200:
        return "白系"
    if r > g + 20 and r > b + 20:
        return "赤系"
    if g > r + 20 and g > b + 20:
        return "緑系"
    if b > r + 20 and b > g + 20:
        return "青系"
    if r > 180 and g > 160 and b < 120:
        return "黄系"
    if r > 180 and b > 150:
        return "ピンク/紫系"
    return "中間色"


def _generate_heuristic_thumbnail_description(image_path: Path) -> str:
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            width, height = img.size
            orientation = "横長" if width >= height else "縦長"
            stat = ImageStat.Stat(img)
            avg = sum(stat.mean) / 3.0
            tone = "明るい" if avg >= 170 else "落ち着いた" if avg >= 90 else "暗い"
            small = img.resize((48, 48))
            colors = small.getcolors(48 * 48) or []
            colors.sort(reverse=True)
            color_labels: List[str] = []
            for count, rgb in colors[:5]:
                name = _color_name(rgb)
                if name not in color_labels:
                    color_labels.append(name)
            color_text = "、".join(color_labels[:3]) or "中間色"
    except Exception:
        return "画像の読み込みに失敗しましたが、サムネイルとして保存されています。"
    return f"{orientation}の画像で、全体的に{tone}トーン。主要な色は{color_text}です（{width}x{height}px）。"




app = FastAPI(title="YouTube Master UI API")
if video_router:
    app.include_router(video_router)
if llm_usage:
    app.include_router(llm_usage.router)
elif _llm_usage_import_error:
    logger.error("Failed to load llm_usage router: %s", _llm_usage_import_error)
try:
    from backend.routers import llm_models

    app.include_router(llm_models.router)
except Exception as e:
    logger.error("Failed to load llm_models router: %s", e)
app.include_router(jobs.router)
app.include_router(swap.router)
app.include_router(params.router)
app.include_router(channel_registry.router)
app.include_router(kb.router)
app.include_router(reading_dict.router)
app.include_router(audio_check.router)
app.include_router(audio_reports.router)
try:
    from backend.routers import audio_review

    app.include_router(audio_review.router)
except Exception as e:
    logger.error("Failed to load audio_review router: %s", e)
app.include_router(health.router)
app.include_router(healthz.router)
app.include_router(lock_metrics.router)
app.include_router(batch_tts.router)
app.include_router(batch_workflow.router)
app.include_router(episode_files.router)
try:
    from backend.routers import prompts

    app.include_router(prompts.router)
except Exception as e:
    logger.error("Failed to load prompts router: %s", e)
try:
    from backend.routers import meta

    app.include_router(meta.router)
except Exception as e:
    logger.error("Failed to load meta router: %s", e)
try:
    from backend.routers import tts_progress
    app.include_router(tts_progress.router)
except Exception as e:
    logger.error("Failed to load tts_progress router: %s", e)

try:
    from backend.routers import tts_text
    app.include_router(tts_text.router)
except Exception as e:
    logger.error("Failed to load tts_text router: %s", e)

try:
    from backend.routers import tts
    app.include_router(tts.router)
except Exception as e:
    logger.error("Failed to load tts router: %s", e)

try:
    from backend.routers import srt
    app.include_router(srt.router)
except Exception as e:
    logger.error("Failed to load srt router: %s", e)

try:
    from backend.routers import assembled
    app.include_router(assembled.router)
except Exception as e:
    logger.error("Failed to load assembled router: %s", e)

try:
    from backend.routers import channel_videos
    app.include_router(channel_videos.router)
except Exception as e:
    logger.error("Failed to load channel_videos router: %s", e)

try:
    from backend.routers import video_state
    app.include_router(video_state.router)
except Exception as e:
    logger.error("Failed to load video_state router: %s", e)

try:
    from backend.routers import planning_csv
    app.include_router(planning_csv.router)
except Exception as e:
    logger.error("Failed to load planning_csv router: %s", e)

try:
    from backend.routers import planning_channel
    app.include_router(planning_channel.router)
except Exception as e:
    logger.error("Failed to load planning_channel router: %s", e)

try:
    from backend.routers import video_planning
    app.include_router(video_planning.router)
except Exception as e:
    logger.error("Failed to load video_planning router: %s", e)

try:
    from backend.routers import human_scripts
    app.include_router(human_scripts.router)
except Exception as e:
    logger.error("Failed to load human_scripts router: %s", e)

try:
    from backend.routers import audio_tts
    app.include_router(audio_tts.router)
except Exception as e:
    logger.error("Failed to load audio_tts router: %s", e)
try:
    from backend.routers import research_files

    app.include_router(research_files.router)
except Exception as e:
    logger.error("Failed to load research_files router: %s", e)

try:
    from backend.routers import ssot_catalog

    app.include_router(ssot_catalog.router)
except Exception as e:
    logger.error("Failed to load ssot_catalog router: %s", e)

try:
    from backend.routers import ssot_docs

    app.include_router(ssot_docs.router)
except Exception as e:
    logger.error("Failed to load ssot_docs router: %s", e)

try:
    from backend.routers import gh_releases_archive

    app.include_router(gh_releases_archive.router)
except Exception as e:
    logger.error("Failed to load gh_releases_archive router: %s", e)

try:
    from backend.routers import agent_org

    app.include_router(agent_org.router)
except Exception as e:
    logger.error("Failed to load agent_org router: %s", e)

try:
    from backend.routers import agent_board

    app.include_router(agent_board.router)
except Exception as e:
    logger.error("Failed to load agent_board router: %s", e)

try:
    from backend.routers import pipeline_boxes

    app.include_router(pipeline_boxes.router)
except Exception as e:
    logger.error("Failed to load pipeline_boxes router: %s", e)

try:
    from backend.routers import remotion

    app.include_router(remotion.router)
except Exception as e:
    logger.error("Failed to load remotion router: %s", e)

try:
    from backend.routers import video_input

    app.include_router(video_input.router)
except Exception as e:
    logger.error("Failed to load video_input router: %s", e)

try:
    from backend.routers import guards

    app.include_router(guards.router)
except Exception as e:
    logger.error("Failed to load guards router: %s", e)

try:
    from backend.routers import redo

    app.include_router(redo.router)
except Exception as e:
    logger.error("Failed to load redo router: %s", e)

try:
    from backend.routers import redo_flags

    app.include_router(redo_flags.router)
except Exception as e:
    logger.error("Failed to load redo_flags router: %s", e)

try:
    from backend.routers import thumbnails

    app.include_router(thumbnails.router)
except Exception as e:
    logger.error("Failed to load thumbnails router: %s", e)

try:
    from backend.routers import thumbnails_qc_notes

    app.include_router(thumbnails_qc_notes.router)
except Exception as e:
    logger.error("Failed to load thumbnails_qc_notes router: %s", e)

try:
    from backend.routers import thumbnails_workspace

    app.include_router(thumbnails_workspace.router)
except Exception as e:
    logger.error("Failed to load thumbnails_workspace router: %s", e)

try:
    from backend.routers import thumbnails_specs

    app.include_router(thumbnails_specs.router)
except Exception as e:
    logger.error("Failed to load thumbnails_specs router: %s", e)

try:
    from backend.routers import thumbnails_templates

    app.include_router(thumbnails_templates.router)
except Exception as e:
    logger.error("Failed to load thumbnails_templates router: %s", e)

try:
    from backend.routers import thumbnails_video

    app.include_router(thumbnails_video.router)
except Exception as e:
    logger.error("Failed to load thumbnails_video router: %s", e)

try:
    from backend.routers import thumbnails_overrides

    app.include_router(thumbnails_overrides.router)
except Exception as e:
    logger.error("Failed to load thumbnails_overrides router: %s", e)

try:
    from backend.routers import thumbnails_assets

    app.include_router(thumbnails_assets.router)
except Exception as e:
    logger.error("Failed to load thumbnails_assets router: %s", e)

try:
    from backend.routers import dashboard

    app.include_router(dashboard.router)
except Exception as e:
    logger.error("Failed to load dashboard router: %s", e)

# NOTE: Do not mount StaticFiles for thumbnails here: it would shadow
# API routes (/thumbnails/library/, /thumbnails/assets/). Use the API routes.

try:
    from backend.routers import publishing

    app.include_router(publishing.router)
except Exception as e:
    logger.error("Failed to load publishing router: %s", e)

try:
    from backend.routers import auto_draft

    app.include_router(auto_draft.router)
except Exception as e:
    logger.error("Failed to load auto_draft router: %s", e)

try:
    from backend.routers import settings

    app.include_router(settings.router)
except Exception as e:
    logger.error("Failed to load settings router: %s", e)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3100",
        "http://127.0.0.1:3100",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
def _build_channel_summary(code: str, info: dict) -> ChannelSummaryResponse:
    branding_payload = info.get("branding")
    branding: Optional[ChannelBranding]
    if isinstance(branding_payload, dict):
        try:
            branding = ChannelBranding(**branding_payload)
        except Exception:
            branding = None
    else:
        branding = None
    youtube_info = info.get("youtube") or {}
    branding_info = branding_payload if isinstance(branding_payload, dict) else {}
    planned_video_numbers = list_planning_video_numbers(code)
    video_numbers = set(planned_video_numbers)
    video_numbers.update(video_dir.name for video_dir in list_video_dirs(code))
    return ChannelSummaryResponse(
        code=code,
        name=info.get("name"),
        description=info.get("description"),
        video_count=len(video_numbers),
        branding=branding,
        spreadsheet_id=info.get("spreadsheet_id"),
        youtube_title=(youtube_info.get("title") or info.get("youtube_title")),
        youtube_handle=(
            youtube_info.get("handle")
            or youtube_info.get("custom_url")
            or info.get("youtube_handle")
            or branding_info.get("handle")
        ),
        video_workflow=_resolve_video_workflow(info),
        genre=infer_channel_genre(info),
    )


def update_channel_profile(channel: str, payload: ChannelProfileUpdateRequest):
    channel_code = normalize_channel_code(channel)
    info_path, info_payload, channel_dir = _load_channel_info_payload(channel_code)
    script_prompt_path = channel_dir / "script_prompt.txt"
    changes: List[Dict[str, Any]] = []
    info_changed = False

    if payload.description is not None:
        new_description = payload.description.strip()
        if info_payload.get("description") != new_description:
            _record_change(changes, "description", info_payload.get("description"), new_description)
            info_payload["description"] = new_description
            info_changed = True

    if payload.script_prompt is not None:
        sanitized_prompt = _sanitize_script_prompt(payload.script_prompt)
        normalized_json_prompt = sanitized_prompt
        existing_prompt = (info_payload.get("script_prompt") or "").strip()
        if existing_prompt != normalized_json_prompt:
            _record_change(changes, "script_prompt", existing_prompt, normalized_json_prompt, redact=True)
            info_payload["script_prompt"] = normalized_json_prompt
            write_text_with_lock(script_prompt_path, sanitized_prompt + "\n")
            info_changed = True

    if payload.default_tags is not None:
        cleaned_tags = _clean_default_tags(payload.default_tags) or []
        current_tags = info_payload.get("default_tags") or []
        if cleaned_tags != current_tags:
            _record_change(changes, "default_tags", current_tags, cleaned_tags)
            if cleaned_tags:
                info_payload["default_tags"] = cleaned_tags
            else:
                info_payload.pop("default_tags", None)
            info_changed = True

    if "benchmarks" in payload.model_fields_set:
        if payload.benchmarks is None:
            if "benchmarks" in info_payload:
                _record_change(changes, "benchmarks", info_payload.get("benchmarks"), None)
                info_payload.pop("benchmarks", None)
                info_changed = True
        else:
            bench_dump = payload.benchmarks.model_dump()
            bench_dump["updated_at"] = datetime.now().strftime("%Y-%m-%d")
            bench_dump["channels"] = sorted(bench_dump.get("channels") or [], key=lambda it: (it.get("handle") or ""))
            bench_dump["script_samples"] = sorted(
                bench_dump.get("script_samples") or [],
                key=lambda it: (it.get("base") or "", it.get("path") or ""),
            )
            current_bench = info_payload.get("benchmarks")
            if bench_dump != current_bench:
                _record_change(changes, "benchmarks", current_bench, bench_dump)
                info_payload["benchmarks"] = bench_dump
                info_changed = True

    youtube_info = info_payload.setdefault("youtube", {})
    if payload.youtube_title is not None:
        new_title = payload.youtube_title.strip()
        if youtube_info.get("title") != new_title:
            _record_change(changes, "youtube.title", youtube_info.get("title"), new_title)
            youtube_info["title"] = new_title
            info_changed = True
        info_payload.pop("youtube_title", None)
    if payload.youtube_description is not None:
        new_desc = payload.youtube_description.strip()
        current_desc = info_payload.get("youtube_description") or ""
        if new_desc:
            if current_desc != new_desc:
                _record_change(changes, "youtube_description", current_desc or None, new_desc)
                info_payload["youtube_description"] = new_desc
                info_changed = True
        else:
            if "youtube_description" in info_payload:
                _record_change(changes, "youtube_description", current_desc or None, None)
                info_payload.pop("youtube_description", None)
                info_changed = True
    if payload.youtube_handle is not None:
        new_handle_raw = payload.youtube_handle.strip()
        if new_handle_raw:
            try:
                normalized_handle = normalize_youtube_handle(new_handle_raw)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"YouTubeハンドルが不正です: {exc}") from exc

            channel_info_map = refresh_channel_info(force=True)
            _ensure_unique_youtube_handle(channel_code, normalized_handle, channel_info_map)

            try:
                resolved = resolve_youtube_channel_id_from_handle(normalized_handle)
            except YouTubeHandleResolutionError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"YouTubeハンドルから channel_id を特定できませんでした: {exc}",
                ) from exc

            if youtube_info.get("handle") != resolved.handle:
                _record_change(changes, "youtube.handle", youtube_info.get("handle"), resolved.handle)
                youtube_info["handle"] = resolved.handle
                youtube_info["custom_url"] = resolved.handle
                info_changed = True
            if youtube_info.get("channel_id") != resolved.channel_id:
                _record_change(changes, "youtube.channel_id", youtube_info.get("channel_id"), resolved.channel_id)
                youtube_info["channel_id"] = resolved.channel_id
                info_changed = True
            if youtube_info.get("url") != resolved.url:
                _record_change(changes, "youtube.url", youtube_info.get("url"), resolved.url)
                youtube_info["url"] = resolved.url
                info_changed = True
            if youtube_info.get("source") != resolved.channel_id:
                _record_change(changes, "youtube.source", youtube_info.get("source"), resolved.channel_id)
                youtube_info["source"] = resolved.channel_id
                info_changed = True
            if resolved.title and youtube_info.get("title") != resolved.title:
                _record_change(changes, "youtube.title", youtube_info.get("title"), resolved.title)
                youtube_info["title"] = resolved.title
                info_changed = True

            branding_info = info_payload.setdefault("branding", {})
            if branding_info.get("handle") != resolved.handle:
                _record_change(changes, "branding.handle", branding_info.get("handle"), resolved.handle)
                branding_info["handle"] = resolved.handle
                branding_info["custom_url"] = resolved.handle
                info_changed = True
            if branding_info.get("url") != resolved.url:
                _record_change(changes, "branding.url", branding_info.get("url"), resolved.url)
                branding_info["url"] = resolved.url
                info_changed = True
            if resolved.title and branding_info.get("title") != resolved.title:
                _record_change(changes, "branding.title", branding_info.get("title"), resolved.title)
                branding_info["title"] = resolved.title
                info_changed = True
            if resolved.avatar_url and branding_info.get("avatar_url") != resolved.avatar_url:
                _record_change(
                    changes,
                    "branding.avatar_url",
                    branding_info.get("avatar_url"),
                    resolved.avatar_url,
                )
                branding_info["avatar_url"] = resolved.avatar_url
                info_changed = True
        else:
            # Allow clearing handle explicitly.
            if youtube_info.get("handle"):
                _record_change(changes, "youtube.handle", youtube_info.get("handle"), None)
                youtube_info.pop("handle", None)
                youtube_info.pop("custom_url", None)
                info_changed = True
        info_payload.pop("youtube_handle", None)

    audio_changed = False
    if payload.audio:
        config_path, voice_payload = _load_voice_config_payload(channel_code, required=True)
        voices = (voice_payload.get("voices") or {}).keys()
        if payload.audio.default_voice_key is not None:
            new_key = payload.audio.default_voice_key.strip()
            if new_key not in voices:
                raise HTTPException(
                    status_code=400,
                    detail=f"voice_config.json に {new_key} が定義されていません。",
                )
            if voice_payload.get("default_voice_key") != new_key:
                _record_change(
                    changes,
                    "audio.default_voice_key",
                    voice_payload.get("default_voice_key"),
                    new_key,
                )
                voice_payload["default_voice_key"] = new_key
                audio_changed = True
        if payload.audio.section_voice_rules is not None:
            cleaned_rules: Dict[str, str] = {}
            for section, key in payload.audio.section_voice_rules.items():
                if section is None or key is None:
                    continue
                section_name = section.strip()
                voice_key = key.strip()
                if not section_name or not voice_key:
                    continue
                if voice_key not in voices:
                    raise HTTPException(
                        status_code=400,
                        detail=f"voice_config.json に {voice_key} が定義されていません。",
                    )
                cleaned_rules[section_name] = voice_key
            current_rules = voice_payload.get("section_voice_rules") or {}
            if cleaned_rules != current_rules:
                _record_change(
                    changes,
                    "audio.section_voice_rules",
                    current_rules,
                    cleaned_rules,
                )
                voice_payload["section_voice_rules"] = cleaned_rules
                audio_changed = True
        if audio_changed and config_path is not None:
            write_text_with_lock(
                config_path, json.dumps(voice_payload, ensure_ascii=False, indent=2) + "\n"
            )

    if info_changed:
        write_text_with_lock(info_path, json.dumps(info_payload, ensure_ascii=False, indent=2) + "\n")
        rebuild_channel_catalog()

    if info_changed or audio_changed:
        _append_channel_profile_log(channel_code, changes)

    return _build_channel_profile_response(channel_code)


def _resolve_channel_title(channel_code: str, info_map: Dict[str, dict]) -> Optional[str]:
    return resolve_channel_title(channel_code, info_map)


def _get_or_create_thumbnail_project(payload: dict, channel_code: str, video_number: str) -> dict:
    projects: List[dict] = payload.setdefault("projects", [])
    for project in projects:
        if (
            project.get("channel", "").strip().upper() == channel_code
            and (_coerce_video_from_dir(str(project.get("video"))) or "").lower() == video_number.lower()
        ):
            project["channel"] = channel_code
            project["video"] = video_number
            project.setdefault("variants", [])
            return project
    project = {
        "channel": channel_code,
        "video": video_number,
        "status": "draft",
        "variants": [],
    }
    projects.append(project)
    return project


def _normalize_thumbnail_status(status: Optional[str]) -> str:
    if not status:
        return "draft"
    lowered = status.strip().lower()
    if lowered not in THUMBNAIL_PROJECT_STATUSES:
        return "draft"
    return lowered


def _normalize_thumbnail_tags(tags: Optional[Iterable[str]]) -> Optional[List[str]]:
    if not tags:
        return None
    normalized = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        trimmed = tag.strip()
        if trimmed:
            normalized.append(trimmed)
    return normalized or None


def _normalize_thumbnail_image_path(channel_code: str, video_number: str, image_path: str) -> str:
    stripped = image_path.strip().lstrip("/")
    if not stripped:
        raise HTTPException(status_code=400, detail="image_path を指定してください。")
    candidate = Path(stripped)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise HTTPException(status_code=400, detail="image_path が不正です。")
    parts = [part for part in candidate.parts if part not in ("", ".")]
    if parts and parts[0].lower() in {"thumbnails", "assets"}:
        parts = parts[1:]
    if parts and parts[0].upper() == channel_code:
        parts = parts[1:]
    if parts and parts[0] == video_number:
        parts = parts[1:]
    relative = Path(*parts) if parts else candidate
    if not relative.name:
        raise HTTPException(status_code=400, detail="image_path にはファイル名を含めてください。")
    safe_parts = [part for part in relative.parts if part not in ("", ".", "..")]
    final_path = Path(channel_code) / video_number
    for part in safe_parts:
        final_path /= part
    return final_path.as_posix()


def _build_thumbnail_image_url(image_path: str) -> str:
    normalized = image_path.lstrip("/")
    return f"/thumbnails/assets/{normalized}"


async def _save_upload_file(upload: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as buffer:
        while True:
            chunk = await upload.read(1 << 20)
            if not chunk:
                break
            buffer.write(chunk)
    await upload.seek(0)


def _ensure_unique_filename(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem or "thumbnail"
    suffix = Path(filename).suffix
    counter = 1
    while True:
        alt = directory / f"{stem}_{counter:02d}{suffix}"
        if not alt.exists():
            return alt
        counter += 1


def _build_thumbnail_template_context(channel_code: str, video_number: str) -> Dict[str, str]:
    ctx: Dict[str, str] = {
        "channel": channel_code,
        "video": video_number,
        "title": "",
        "sheet_title": "",
        "thumbnail_upper": "",
        "thumbnail_lower": "",
        "thumbnail_prompt": "",
        "dalle_prompt": "",
        "summary": "",
        "notes": "",
    }

    # status.json (if present)
    try:
        status = load_status(channel_code, video_number)
        metadata = status.get("metadata") if isinstance(status, dict) else None
        if isinstance(metadata, dict):
            ctx["title"] = str(metadata.get("title") or metadata.get("video_title") or "") or ctx["title"]
            ctx["sheet_title"] = str(metadata.get("sheet_title") or "") or ctx["sheet_title"]
            ctx["summary"] = str(metadata.get("summary") or "") or ctx["summary"]
            ctx["notes"] = str(metadata.get("notes") or "") or ctx["notes"]
    except Exception:
        pass

    # Planning CSV (if present)
    try:
        for row in planning_store.get_rows(channel_code, force_refresh=True):
            if normalize_video_number(row.video_number or "") != video_number:
                continue
            raw = row.raw
            if not isinstance(raw, dict):
                break
            ctx["title"] = str(raw.get("タイトル") or "") or ctx["title"]
            ctx["thumbnail_upper"] = str(raw.get("サムネタイトル上") or "") or ctx["thumbnail_upper"]
            ctx["thumbnail_lower"] = str(raw.get("サムネタイトル下") or "") or ctx["thumbnail_lower"]
            ctx["thumbnail_prompt"] = str(raw.get("サムネ画像プロンプト（URL・テキスト指示込み）") or "") or ctx["thumbnail_prompt"]
            ctx["dalle_prompt"] = str(raw.get("DALL-Eプロンプト（URL・テキスト指示込み）") or "") or ctx["dalle_prompt"]
            break
    except Exception:
        pass

    # Fill title from sheet_title if needed
    if not ctx["title"] and ctx["sheet_title"]:
        ctx["title"] = ctx["sheet_title"]

    # Normalize whitespace
    for key, value in list(ctx.items()):
        if value is None:
            ctx[key] = ""
            continue
        ctx[key] = str(value).strip()
    return ctx


def _render_thumbnail_prompt_template(template: str, context: Dict[str, str]) -> str:
    """
    Simple placeholder rendering: replaces `{{key}}` with values from context.
    """
    rendered = template or ""
    for key, value in context.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value or "")
    return rendered


def _normalize_thumbnail_image_bytes(image_bytes: bytes, *, width: int = 1280, height: int = 720) -> bytes:
    """
    Normalize arbitrary generated images to a YouTube thumbnail-friendly 16:9 PNG.
    - center-crop to 16:9
    - resize to 1280x720
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            src_w, src_h = img.size
            if src_w <= 0 or src_h <= 0:
                raise ValueError("invalid image size")
            target_ratio = width / height
            src_ratio = src_w / src_h
            if src_ratio > target_ratio:
                # too wide → crop left/right
                new_w = int(src_h * target_ratio)
                left = max(0, (src_w - new_w) // 2)
                img = img.crop((left, 0, left + new_w, src_h))
            elif src_ratio < target_ratio:
                # too tall → crop top/bottom
                new_h = int(src_w / target_ratio)
                top = max(0, (src_h - new_h) // 2)
                img = img.crop((0, top, src_w, top + new_h))
            img = img.resize((width, height), Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="PNG", optimize=True)
            return out.getvalue()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"生成画像の正規化に失敗しました: {exc}") from exc


def _sanitize_library_filename(name: str, *, default_prefix: str) -> str:
    safe_name = Path(name or "").name
    if not safe_name:
        safe_name = default_prefix
    suffix = Path(safe_name).suffix.lower()
    if suffix not in THUMBNAIL_SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"ファイル拡張子は {', '.join(sorted(THUMBNAIL_SUPPORTED_EXTENSIONS))} のみ利用できます。",
        )
    stem = Path(safe_name).stem or default_prefix
    stem = re.sub(r"[^\w.-]", "_", stem)
    if not stem:
        stem = default_prefix
    return f"{stem}{suffix}"


def _persist_thumbnail_variant(
    channel_code: str,
    video_number: str,
    *,
    label: str,
    status: Optional[str] = None,
    image_url: Optional[str] = None,
    image_path: Optional[str] = None,
    notes: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    prompt: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    model_key: Optional[str] = None,
    openrouter_generation_id: Optional[str] = None,
    cost_usd: Optional[float] = None,
    usage: Optional[Dict[str, Any]] = None,
    make_selected: bool = False,
) -> ThumbnailVariantResponse:
    normalized_status = _normalize_thumbnail_status(status)
    normalized_tags = _normalize_thumbnail_tags(tags)
    if image_path:
        normalized_path = _normalize_thumbnail_image_path(channel_code, video_number, image_path)
        image_path = normalized_path
        if not image_url:
            image_url = _build_thumbnail_image_url(image_path)
    if not image_url and not image_path:
        raise HTTPException(status_code=400, detail="画像URLまたは画像パスを指定してください。")
    now = datetime.now(timezone.utc).isoformat()
    variant_id = f"ytm::{uuid.uuid4().hex[:12]}"
    variant_doc = {
        "id": variant_id,
        "label": label[:120],
        "status": normalized_status,
        "image_url": image_url,
        "image_path": image_path,
        "notes": notes,
        "tags": normalized_tags,
        "prompt": prompt,
        "created_at": now,
        "updated_at": now,
    }
    if provider:
        variant_doc["provider"] = str(provider)
    if model:
        variant_doc["model"] = str(model)
    if model_key:
        variant_doc["model_key"] = str(model_key)
    if openrouter_generation_id:
        variant_doc["openrouter_generation_id"] = str(openrouter_generation_id)
    if cost_usd is not None:
        variant_doc["cost_usd"] = float(cost_usd)
    if usage:
        variant_doc["usage"] = usage
    with THUMBNAIL_PROJECTS_LOCK:
        path, payload = _load_thumbnail_projects_document()
        project = _get_or_create_thumbnail_project(payload, channel_code, video_number)
        variants: List[dict] = project.setdefault("variants", [])
        variants.append(variant_doc)
        if make_selected:
            project["selected_variant_id"] = variant_id
        project["updated_at"] = now
        _write_thumbnail_projects_document(path, payload)
    return ThumbnailVariantResponse(
        id=variant_id,
        label=variant_doc["label"],
        status=normalized_status,
        image_url=image_url,
        image_path=image_path,
        preview_url=image_url,
        notes=notes,
        tags=normalized_tags,
        provider=variant_doc.get("provider"),
        model=variant_doc.get("model"),
        model_key=variant_doc.get("model_key"),
        openrouter_generation_id=variant_doc.get("openrouter_generation_id"),
        cost_usd=variant_doc.get("cost_usd"),
        usage=variant_doc.get("usage"),
        is_selected=make_selected,
        created_at=now,
        updated_at=now,
    )

def _utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_thumbnail_stable_id(raw: Optional[str]) -> Optional[str]:
    """
    Normalize a "stable output id" such as `00_thumb_1` / `00_thumb_2`.

    Accepts:
      - 00_thumb_1
      - 00_thumb_1.png / 00_thumb_1.jpg / 00_thumb_1.webp
      - thumb_1 / thumb_2 (legacy labels)
      - a / b (legacy labels)
      - default / __default__ / 00_thumb / thumb (treated as non-stable / canonical output)
    """
    if raw is None:
        return None
    value = str(raw or "").strip()
    if not value:
        return None
    cleaned = value.split("?", 1)[0].split("#", 1)[0].strip()
    if not cleaned:
        return None
    base = Path(cleaned).name.strip()
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    lowered = base.strip().lower()
    if lowered in {"default", "__default__", "00_thumb", "thumb"}:
        return None
    if lowered in {"thumb_1", "thumb1", "1", "a"}:
        return "00_thumb_1"
    if lowered in {"thumb_2", "thumb2", "2", "b"}:
        return "00_thumb_2"
    normalized = base.strip()
    if normalized.startswith("00_thumb_") and normalized[len("00_thumb_") :].isdigit():
        return normalized

    # Be permissive: accept strings that contain a stable id (e.g. "00_thumb_1 (selected)").
    match = re.search(r"(00_thumb_\d+)", lowered)
    if match:
        return match.group(1)

    # Also accept strings containing legacy labels like "thumb_1 (selected)".
    match = re.search(r"(?:^|[^a-z0-9])(thumb[_-]?(1|2))(?:$|[^a-z0-9])", lowered)
    if match:
        return "00_thumb_1" if match.group(2) == "1" else "00_thumb_2"

    raise HTTPException(
        status_code=400,
        detail="stable must be like 00_thumb_1 / 00_thumb_2 (or 00_thumb_<n>)",
    )


def _thumb_spec_stable_path(channel_code: str, video_number: str, stable: str) -> Path:
    return THUMBNAIL_ASSETS_DIR / channel_code / video_number / f"thumb_spec.{stable}.json"


def _text_line_spec_stable_path(channel_code: str, video_number: str, stable: str) -> Path:
    return THUMBNAIL_ASSETS_DIR / channel_code / video_number / f"text_line_spec.{stable}.json"


def _elements_spec_stable_path(channel_code: str, video_number: str, stable: str) -> Path:
    return THUMBNAIL_ASSETS_DIR / channel_code / video_number / f"elements_spec.{stable}.json"


def parse_cli_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the YouTube Master UI backend")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (development only)")
    parser.add_argument(
        "--reload-dir",
        dest="reload_dirs",
        action="append",
        default=None,
        help="Additional directories to watch when auto-reload is enabled (can be specified multiple times)",
    )
    parser.add_argument("--log-level", default="info", help="Uvicorn log level (default: info)")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_cli_args(argv)
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=args.reload_dirs,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()


def _generate_thumbnail_caption(image_path: Path) -> tuple[str, Optional[str], str]:
    prompt = (
        "以下のYouTubeサムネイル画像の内容を80文字前後の日本語で説明してください。"
        "人物・背景・文字・雰囲気を具体的に触れてください。"
    )
    try:
        raw_bytes = image_path.read_bytes()
    except OSError as exc:
        logger.warning("Failed to read image for caption: %s", exc)
        raise HTTPException(status_code=500, detail=f"画像の読み込みに失敗しました: {exc}") from exc

    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    data_url = f"data:{mime_type};base64,{base64.b64encode(raw_bytes).decode('ascii')}"
    messages: List[Dict[str, object]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]

    try:
        from factory_common.llm_router import get_router
    except Exception as exc:  # pragma: no cover - optional dependency mismatch
        logger.warning("LLMRouter is not available for thumbnail caption: %s", exc)
        return _generate_heuristic_thumbnail_description(image_path), None, "heuristic"

    router = get_router()
    try:
        result = router.call_with_raw(
            task="visual_thumbnail_caption",
            messages=messages,
        )
    except SystemExit as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Thumbnail caption LLM failed; falling back to heuristic: %s", exc)
        return _generate_heuristic_thumbnail_description(image_path), None, "heuristic"

    provider = str(result.get("provider") or "").strip().lower()
    content = result.get("content")
    if isinstance(content, list):
        text = " ".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
    else:
        text = str(content or "").strip()
    if provider == "agent" and not text:
        raise HTTPException(status_code=409, detail="THINK MODE の結果がまだありません。agent_runner で完了してください。")
    if not text:
        return _generate_heuristic_thumbnail_description(image_path), None, "heuristic"

    model_key = str(result.get("model") or "").strip()
    model_name: Optional[str] = model_key or None
    try:
        model_conf = (router.config.get("models", {}) or {}).get(model_key, {}) if model_key else {}
        if isinstance(model_conf, dict):
            if provider == "azure":
                model_name = model_conf.get("deployment") or model_name
            else:
                model_name = model_conf.get("model_name") or model_name
    except Exception:
        pass

    if provider == "agent":
        return text, None, "think_mode"
    if provider == "azure":
        source = "openai"
    else:
        source = "openrouter"
    return text, model_name, source
