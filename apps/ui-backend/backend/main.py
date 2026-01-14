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
import contextlib
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
import tempfile
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
from backend.audio.script_loader import iterate_sections
from backend.core.tools.content_processor import ContentProcessor
from backend.core.tools.audio_manager import AudioManager
from backend.core.tools.channel_profile import load_channel_profile
from backend.core.tools.channel_stats_store import merge_channel_stats_into_channel_info, write_channel_stats
from backend.core.tools.prompt_utils import auto_placeholder_values
from backend.core.tools import thumbnails_lookup as thumbnails_lookup_tools
# 移行先: script_pipeline/tools 配下の簡易実装を利用
from script_pipeline.tools import planning_requirements, planning_store
from script_pipeline.tools import openrouter_models as openrouter_model_utils
from backend.app.channel_info_store import (
    CHANNELS_DIR,
    CHANNEL_INFO_PATH,
    find_channel_directory,
    infer_channel_genre,
    refresh_channel_info,
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
from backend.app.planning_payload import build_planning_payload, build_planning_payload_from_row
from backend.app.youtube_client import YouTubeDataClient, YouTubeDataAPIError
from backend.app.redo_models import RedoUpdateRequest, RedoUpdateResponse
from backend.app.thumbnails_models import ThumbnailOverrideRequest, ThumbnailOverrideResponse
from backend.app.srt_models import SRTIssue, SRTVerifyResponse
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
    video_pkg_root,
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
VIDEO_PIPELINE_ROOT = video_pkg_root()
DATA_ROOT = ssot_script_data_root()
EXPORTS_DIR = SCRIPT_PIPELINE_ROOT / "exports"
CHANNEL_PLANNING_DIR = ssot_planning_root() / "channels"
# Legacy single-file planning CSV override (kept for older tests/tooling).
PLANNING_CSV_PATH: Path | None = None
SPREADSHEET_EXPORT_DIR = EXPORTS_DIR / "spreadsheets"
THUMBNAIL_PROJECTS_CANDIDATES = [
    ssot_thumbnails_root() / "projects.json",
]
THUMBNAIL_TEMPLATES_CANDIDATES = [
    ssot_thumbnails_root() / "templates.json",
]
THUMBNAIL_ASSETS_DIR = ssot_thumbnails_root() / "assets"
THUMBNAIL_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
THUMBNAIL_PROJECTS_LOCK = threading.Lock()
THUMBNAIL_TEMPLATES_LOCK = threading.Lock()
VIDEO_CHANNEL_PRESETS_PATH = VIDEO_PIPELINE_ROOT / "config" / "channel_presets.json"
VIDEO_CHANNEL_PRESETS_LOCK = threading.Lock()
IMAGE_MODEL_KEY_BLOCKLIST = {
    # Policy: Gemini 3 image models are blocked for video images, but allowed for thumbnails.
    "gemini_3_pro_image_preview",
    "openrouter_gemini_3_pro_image_preview",
}


def _image_model_key_blocked(model_key: str, *, task: Optional[str]) -> bool:
    mk = str(model_key or "").strip()
    if not mk:
        return False
    if mk not in IMAGE_MODEL_KEY_BLOCKLIST:
        return False
    # Thumbnails are allowed to use Gemini 3 (explicitly).
    if str(task or "").strip() == "thumbnail_image_gen":
        return False
    return True
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_GENERATION_URL = "https://openrouter.ai/api/v1/generation"
OPENROUTER_MODELS_CACHE_LOCK = threading.Lock()
OPENROUTER_MODELS_CACHE: Dict[str, Any] = {"fetched_at": 0.0, "pricing_by_id": {}}
OPENROUTER_MODELS_CACHE_TTL_SEC = 60 * 60
CODEX_CONFIG_TOML_PATH = Path.home() / ".codex" / "config.toml"
CODEX_EXEC_CONFIG_PATH = PROJECT_ROOT / "configs" / "codex_exec.yaml"
CODEX_EXEC_LOCAL_CONFIG_PATH = PROJECT_ROOT / "configs" / "codex_exec.local.yaml"
THUMBNAIL_PROJECT_STATUSES = {
    "draft",
    "in_progress",
    "review",
    "approved",
    "published",
    "archived",
}
THUMBNAIL_LIBRARY_MAX_BYTES = 15 * 1024 * 1024
THUMBNAIL_REMOTE_FETCH_TIMEOUT = 15
LOGS_ROOT = ssot_logs_root()
SSOT_SYNC_LOG_DIR = LOGS_ROOT / "regression" / "ssot_sync"
CODEX_SETTINGS_LOCK = threading.Lock()


from backend.app.planning_csv_store import (  # noqa: E402
    _maybe_int_from_token,
    _normalize_video_number_token,
    _read_channel_csv_rows,
    _write_csv_with_lock,
)


PROGRESS_STATUS_PATH = DATA_ROOT / "_progress" / "processing_status.json"
AUDIO_CHANNELS_DIR = SCRIPT_PIPELINE_ROOT / "audio" / "channels"
LOCK_TIMEOUT_SECONDS = 5.0
VALID_STAGE_STATUSES = {"pending", "in_progress", "blocked", "review", "completed"}
MAX_STATUS_LENGTH = 64
LOCK_METRICS = {"timeout": 0, "unexpected": 0}
LOCK_HISTORY: deque[dict] = deque(maxlen=50)
LOCK_DB_PATH = LOGS_ROOT / "lock_metrics.db"
LOCK_ALERT_CONFIG_PATH = PROJECT_ROOT / "configs" / "ui_lock_alerts.json"
LOCK_ALERT_CONFIG = {
    "enabled": False,
    "timeout_threshold": None,
    "unexpected_threshold": None,
    "cooldown_minutes": 30,
    "slack_webhook": None,
}
LOCK_ALERT_STATE = {
    "timeout": 0,
    "unexpected": 0,
    "last_alert_at": None,
}

def _fetch_openrouter_model_ids_via_rest(api_key: str) -> List[str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    referer = os.getenv("OPENROUTER_REFERRER")
    title = os.getenv("OPENROUTER_TITLE")
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    try:
        response = requests.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=30)
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise HTTPException(status_code=502, detail=f"OpenRouter モデル一覧の取得に失敗しました: {exc}") from exc
    if response.status_code == 401:
        raise HTTPException(status_code=400, detail="OpenRouter APIキーが無効です。")
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"OpenRouter モデル一覧取得エラー: {response.text}")
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="OpenRouter モデル一覧が不正な形式でした。") from exc
    models: List[str] = []
    for entry in data.get("data") or []:
        model_id = entry.get("id")
        if isinstance(model_id, str):
            models.append(model_id)
    return models


def _list_openai_model_ids(api_key: str) -> List[str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.get("https://api.openai.com/v1/models", headers=headers, timeout=30)
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise HTTPException(status_code=502, detail=f"OpenAI モデル一覧の取得に失敗しました: {exc}") from exc
    if response.status_code == 401:
        raise HTTPException(status_code=400, detail="OpenAI APIキーが無効です。")
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"OpenAI モデル一覧取得エラー: {response.text}")
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="OpenAI モデル一覧が不正な形式でした。") from exc
    models = []
    for entry in data.get("data") or []:
        model_id = entry.get("id")
        if isinstance(model_id, str):
            models.append(model_id)
    return models


def _list_openrouter_model_ids(api_key: str) -> List[str]:
    """Return OpenRouter model IDs prioritizing recommended free tiers, but still exposing the full catalog."""
    curated_models: List[str] = []
    previous_key = os.environ.get("OPENROUTER_API_KEY")
    try:
        os.environ["OPENROUTER_API_KEY"] = api_key
        curated_models = openrouter_model_utils.get_free_model_candidates(refresh=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load OpenRouter free model list via 01_secretary logic: %s", exc)
    finally:
        if previous_key is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = previous_key
    all_models: List[str] = []
    try:
        all_models = _fetch_openrouter_model_ids_via_rest(api_key)
    except HTTPException as exc:
        if not curated_models:
            raise
        logger.warning("OpenRouter REST model list failed, falling back to curated list only: %s", exc.detail)
    merged: List[str] = []
    for model_id in curated_models + all_models:
        if isinstance(model_id, str) and model_id not in merged:
            merged.append(model_id)
    return merged or curated_models or all_models

CHANNEL_PROFILE_LOG_DIR = LOGS_ROOT / "regression"
THUMBNAIL_QUICK_HISTORY_PATH = LOGS_ROOT / "regression" / "thumbnail_quick_history.jsonl"
THUMBNAIL_QUICK_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

YOUTUBE_UPLOAD_CACHE_DIR = DATA_ROOT / "_cache" / "youtube_uploads"
YOUTUBE_UPLOAD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
YOUTUBE_UPLOAD_CACHE: Dict[str, tuple[datetime, list["ThumbnailChannelVideoResponse"]]] = {}
YOUTUBE_UPLOAD_CACHE_TTL = timedelta(
    hours=float(os.getenv("YOUTUBE_UPLOAD_CACHE_TTL_HOURS", "6"))
)
YOUTUBE_UPLOAD_BACKOFF = timedelta(
    hours=float(os.getenv("YOUTUBE_UPLOAD_BACKOFF_HOURS", "12"))
)
YOUTUBE_UPLOADS_MAX_REFRESH_PER_REQUEST = int(
    os.getenv("YOUTUBE_UPLOADS_MAX_REFRESH_PER_REQUEST", "2")
)
YOUTUBE_UPLOAD_FAILURE_STATE: Dict[str, datetime] = {}
YOUTUBE_BRANDING_TTL = timedelta(
    hours=float(os.getenv("YOUTUBE_BRANDING_TTL_HOURS", "24"))
)
YOUTUBE_BRANDING_BACKOFF: Dict[str, datetime] = {}

if not os.getenv("YOUTUBE_API_KEY"):
    _load_env_value("YOUTUBE_API_KEY")

def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

def _load_cached_uploads(channel_key: str) -> tuple[Optional[datetime], list["ThumbnailChannelVideoResponse"]]:
    path = YOUTUBE_UPLOAD_CACHE_DIR / f"{channel_key}.json"
    if not path.exists():
        return None, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - tolerate cache corruption
        logger.warning("Failed to read thumbnail cache for %s: %s", channel_key, exc)
        return None, []
    fetched_at = _parse_iso_datetime(payload.get("fetched_at"))
    videos_payload = payload.get("videos") or []
    videos: list[ThumbnailChannelVideoResponse] = []
    for item in videos_payload:
        try:
            videos.append(ThumbnailChannelVideoResponse.model_validate(item))
        except Exception:
            continue
    return fetched_at, videos

def _save_cached_uploads(channel_key: str, fetched_at: datetime, videos: list["ThumbnailChannelVideoResponse"]):
    payload = {
        "fetched_at": fetched_at.replace(tzinfo=timezone.utc).isoformat(),
        "videos": [video.model_dump() for video in videos],
    }
    path = YOUTUBE_UPLOAD_CACHE_DIR / f"{channel_key}.json"
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - disk issues
        logger.warning("Failed to persist thumbnail cache for %s: %s", channel_key, exc)
STAGE_ORDER = [
    "topic_research",
    "script_outline",
    "script_draft",
    "script_enhancement",
    "script_review",
    "quality_check",
    "script_validation",
    "script_polish_ai",
    "script_tts_prepare",
    "audio_synthesis",
    "srt_generation",
    "timeline_copy",
    "image_generation",
]

YOUTUBE_CLIENT = YouTubeDataClient.from_env()
if YOUTUBE_CLIENT is None:
    logger.warning("YOUTUBE_API_KEY が設定されていないため、YouTube Data API からのサムネイル取得をスキップします。ローカル案のプレビューにフォールバックします。")


def init_lock_storage() -> None:
    LOCK_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(LOCK_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lock_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timeout_total INTEGER NOT NULL,
                unexpected_total INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_lock_metrics_time
            ON lock_metrics(occurred_at)
            """
        )
    load_lock_history()
    load_lock_alert_config()


def load_lock_history() -> None:
    LOCK_HISTORY.clear()
    if not LOCK_DB_PATH.exists():
        return
    with sqlite3.connect(LOCK_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT occurred_at, event_type, timeout_total, unexpected_total
            FROM lock_metrics
            ORDER BY occurred_at DESC
            LIMIT 50
            """
        ).fetchall()
    if rows:
        last = rows[0]
        LOCK_METRICS["timeout"] = last[2]
        LOCK_METRICS["unexpected"] = last[3]
    else:
        LOCK_METRICS["timeout"] = 0
        LOCK_METRICS["unexpected"] = 0
    for occurred_at, event_type, timeout_total, unexpected_total in reversed(rows):
        LOCK_HISTORY.append(
            {
                "timestamp": occurred_at,
                "type": event_type,
                "timeout": timeout_total,
                "unexpected": unexpected_total,
            }
        )


def load_lock_alert_config() -> None:
    LOCK_ALERT_CONFIG.update(
        {
            "enabled": False,
            "timeout_threshold": None,
            "unexpected_threshold": None,
            "cooldown_minutes": 30,
            "slack_webhook": None,
        }
    )
    if not LOCK_ALERT_CONFIG_PATH.exists():
        return
    try:
        data = json.loads(LOCK_ALERT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Invalid lock alert configuration: %s", LOCK_ALERT_CONFIG_PATH)
        return
    LOCK_ALERT_CONFIG["enabled"] = bool(data.get("enabled", True))
    LOCK_ALERT_CONFIG["timeout_threshold"] = data.get("timeout_threshold")
    LOCK_ALERT_CONFIG["unexpected_threshold"] = data.get("unexpected_threshold")
    LOCK_ALERT_CONFIG["cooldown_minutes"] = data.get("cooldown_minutes", 30)
    LOCK_ALERT_CONFIG["slack_webhook"] = data.get("slack_webhook")
    reset_lock_alert_state()


def reset_lock_alert_state() -> None:
    LOCK_ALERT_STATE["timeout"] = 0
    LOCK_ALERT_STATE["unexpected"] = 0
    LOCK_ALERT_STATE["last_alert_at"] = None


def emit_lock_alert(message: str) -> None:
    logger.warning("LOCK ALERT: %s", message)
    webhook = LOCK_ALERT_CONFIG.get("slack_webhook")
    if not webhook:
        return
    try:
        payload = json.dumps({"text": message}).encode("utf-8")
        request = urllib.request.Request(
            webhook,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=5)
    except Exception:  # pragma: no cover - network errors not deterministic
        logger.exception("Failed to send Slack notification")


def maybe_emit_lock_alert(event_type: str) -> None:
    if not LOCK_ALERT_CONFIG.get("enabled", False):
        return
    LOCK_ALERT_STATE[event_type] += 1
    threshold_hit = False
    timeout_threshold = LOCK_ALERT_CONFIG.get("timeout_threshold")
    unexpected_threshold = LOCK_ALERT_CONFIG.get("unexpected_threshold")
    if timeout_threshold:
        if LOCK_ALERT_STATE["timeout"] >= timeout_threshold:
            threshold_hit = True
    if unexpected_threshold:
        if LOCK_ALERT_STATE["unexpected"] >= unexpected_threshold:
            threshold_hit = True
    if not threshold_hit:
        return
    last_alert = LOCK_ALERT_STATE.get("last_alert_at")
    cooldown_minutes = LOCK_ALERT_CONFIG.get("cooldown_minutes") or 0
    if last_alert:
        elapsed = datetime.now(timezone.utc) - last_alert
        if elapsed.total_seconds() < cooldown_minutes * 60:
            return
    message = (
        "ロック競合アラート: "
        f"timeout={LOCK_ALERT_STATE['timeout']}件, unexpected={LOCK_ALERT_STATE['unexpected']}件"
        " (閾値到達)"
    )
    emit_lock_alert(message)
    LOCK_ALERT_STATE["last_alert_at"] = datetime.now(timezone.utc)
    reset_lock_alert_state()


def record_lock_event(event_type: str) -> None:
    entry = {
        "timestamp": current_timestamp(),
        "type": event_type,
        "timeout": LOCK_METRICS["timeout"],
        "unexpected": LOCK_METRICS["unexpected"],
    }
    LOCK_HISTORY.append(entry)
    try:
        with sqlite3.connect(LOCK_DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO lock_metrics (occurred_at, event_type, timeout_total, unexpected_total)
                VALUES (?, ?, ?, ?)
                """,
                (entry["timestamp"], entry["type"], entry["timeout"], entry["unexpected"]),
            )
    except sqlite3.Error:
        logger.exception("Failed to persist lock metric event")
    maybe_emit_lock_alert(event_type)


def write_text_with_lock(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with portalocker.Lock(
            str(path),
            mode="w",
            encoding="utf-8",
            timeout=LOCK_TIMEOUT_SECONDS,
        ) as handle:
            handle.write(content)
            handle.flush()
    except portalocker.exceptions.Timeout as exc:
        LOCK_METRICS["timeout"] += 1
        record_lock_event("timeout")
        logger.warning("Lock timeout while writing %s", path)
        raise HTTPException(
            status_code=423,
            detail="ファイルが使用中です。数秒後に再試行してください。",
        ) from exc
    except portalocker.exceptions.LockException as exc:
        LOCK_METRICS["unexpected"] += 1
        record_lock_event("unexpected")
        logger.exception("Unexpected lock error for %s", path)
        raise HTTPException(
            status_code=500,
            detail="ファイルの更新中に予期しないロックエラーが発生しました。",
        ) from exc


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


def rebuild_channel_catalog() -> None:
    entries: List[Dict[str, Any]] = []
    for entry in sorted(CHANNELS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        info_path = entry / "channel_info.json"
        if not info_path.exists():
            continue
        try:
            data = json.loads(info_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("channel_info.json の解析に失敗しました: %s", info_path)
            continue
        entries.append(data)
    write_text_with_lock(
        CHANNEL_INFO_PATH,
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
    )


def _deep_merge_dict(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(base or {})
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)  # type: ignore[arg-type]
        else:
            out[key] = value
    return out


_CODEX_PROFILE_HEADER_RE = re.compile(r"^\s*\[profiles\.(?P<name>[^\]]+)\]\s*$")
_CODEX_PROFILE_KV_RE = re.compile(
    r"^\s*(?P<key>model|model_reasoning_effort)\s*=\s*(?P<value>.+?)\s*(?P<comment>#.*)?$"
)
_ALLOWED_CODEX_REASONING_EFFORT = ["low", "medium", "high", "xhigh"]


def _toml_escape_string(value: str) -> str:
    return str(value).replace("\\\\", "\\\\\\\\").replace('"', '\\"')


def _toml_unquote_string(raw: str) -> str:
    s = str(raw or "").strip()
    # Strip trailing comment, if any (caller may already do this).
    if "#" in s:
        s = s.split("#", 1)[0].rstrip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = s[1:-1]
        # Minimal unescape for common cases.
        inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return s


def _parse_codex_profiles_from_toml(text: str) -> Dict[str, Dict[str, Optional[str]]]:
    profiles: Dict[str, Dict[str, Optional[str]]] = {}
    current: Optional[str] = None
    for line in (text or "").splitlines():
        m = _CODEX_PROFILE_HEADER_RE.match(line)
        if m:
            current = str(m.group("name") or "").strip()
            if current:
                profiles.setdefault(current, {})
            continue
        if not current:
            continue
        kv = _CODEX_PROFILE_KV_RE.match(line)
        if not kv:
            continue
        key = str(kv.group("key") or "").strip()
        value = _toml_unquote_string(kv.group("value") or "")
        if key:
            profiles.setdefault(current, {})[key] = value
    # Normalize keys
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for name, conf in profiles.items():
        out[name] = {
            "model": (conf.get("model") or None),
            "model_reasoning_effort": (conf.get("model_reasoning_effort") or None),
        }
    return out


def _upsert_codex_profile_kv(text: str, *, profile: str, kvs: Dict[str, str]) -> str:
    """Surgical TOML update for `[profiles.<name>]` keeping unrelated content intact."""
    profile = str(profile or "").strip()
    if not profile:
        return text
    want = {k: str(v) for k, v in (kvs or {}).items() if str(v).strip()}
    if not want:
        return text

    lines = (text or "").splitlines(keepends=True)
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        m = _CODEX_PROFILE_HEADER_RE.match(line.rstrip("\r\n"))
        if not m:
            continue
        name = str(m.group("name") or "").strip()
        if start is None and name == profile:
            start = i
            continue
        if start is not None:
            end = i
            break

    def _format_kv(key: str, value: str, *, indent: str = "", comment: str = "") -> str:
        esc = _toml_escape_string(value)
        tail = f" {comment.strip()}" if comment and comment.strip().startswith("#") else (comment or "")
        return f'{indent}{key} = "{esc}"{tail}\n'

    if start is None:
        # Append a new profile section at the end.
        out = list(lines)
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        if out and out[-1].strip() != "":
            out.append("\n")
        out.append(f"[profiles.{profile}]\n")
        for key, value in want.items():
            out.append(_format_kv(key, value))
        return "".join(out)

    existing_keys: set[str] = set()
    updated = []
    body = []
    for line in lines[start + 1 : end]:
        m = _CODEX_PROFILE_KV_RE.match(line.rstrip("\r\n"))
        if m:
            key = str(m.group("key") or "").strip()
            if key in want:
                indent = re.match(r"^\s*", line).group(0) if line else ""
                comment = m.group("comment") or ""
                body.append(_format_kv(key, want[key], indent=indent, comment=comment))
                existing_keys.add(key)
                updated.append(key)
                continue
            existing_keys.add(key)
        body.append(line)

    # Append missing keys at the end of the profile block.
    for key, value in want.items():
        if key not in existing_keys:
            body.append(_format_kv(key, value))

    return "".join([*lines[: start + 1], *body, *lines[end:]])


def _load_codex_exec_config_doc() -> Dict[str, Any]:
    base_doc: Dict[str, Any] = {}
    local_doc: Dict[str, Any] = {}
    if CODEX_EXEC_CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(CODEX_EXEC_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                base_doc = raw
        except Exception:
            base_doc = {}
    if CODEX_EXEC_LOCAL_CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(CODEX_EXEC_LOCAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                local_doc = raw
        except Exception:
            local_doc = {}
    return _deep_merge_dict(base_doc, local_doc)


def _write_codex_exec_local_config(patch: Dict[str, Any]) -> None:
    CODEX_EXEC_LOCAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    current: Dict[str, Any] = {}
    if CODEX_EXEC_LOCAL_CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(CODEX_EXEC_LOCAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                current = raw
        except Exception:
            current = {}
    merged = _deep_merge_dict(current, patch or {})
    CODEX_EXEC_LOCAL_CONFIG_PATH.write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _load_sources_doc() -> Dict[str, Any]:
    """
    Load channel registry sources (same policy as script_pipeline.runner):
    - primary: repo-root `configs/sources.yaml`
    - overlay: packages/script_pipeline/config/sources.yaml
    """
    global_doc: Dict[str, Any] = {}
    local_doc: Dict[str, Any] = {}
    try:
        raw = yaml.safe_load((PROJECT_ROOT / "configs" / "sources.yaml").read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            global_doc = raw
    except Exception:
        global_doc = {}

    try:
        local_path = script_pkg_root() / "config" / "sources.yaml"
        raw = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            local_doc = raw
    except Exception:
        local_doc = {}

    return _deep_merge_dict(global_doc, local_doc)


def _resolve_channel_target_chars(channel_code: str) -> Tuple[int, int]:
    sources = _load_sources_doc()
    channels = sources.get("channels") or {}
    if not isinstance(channels, dict):
        return (8000, 12000)
    entry = channels.get(channel_code.upper()) or {}
    if not isinstance(entry, dict):
        return (8000, 12000)

    def _as_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except Exception:
            return None

    chars_min = _as_int(entry.get("target_chars_min")) or 8000
    chars_max = _as_int(entry.get("target_chars_max")) or 12000
    if chars_max < chars_min:
        chars_max = chars_min
    return (chars_min, chars_max)


def _resolve_channel_chapter_count(channel_code: str) -> Optional[int]:
    sources = _load_sources_doc()
    channels = sources.get("channels") or {}
    if not isinstance(channels, dict):
        return None
    entry = channels.get(channel_code.upper()) or {}
    if not isinstance(entry, dict):
        return None
    raw = entry.get("chapter_count")
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except Exception:
        return None
    return value if value >= 1 else None


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


def current_timestamp() -> str:
    """Return an ISO8601 UTC timestamp with ``Z`` suffix."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def current_timestamp_compact() -> str:
    """Return a compact UTC timestamp used by existing metadata fields."""

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid JSON: {path}") from exc


def write_json(path: Path, payload: dict) -> None:
    write_text_with_lock(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def list_channel_dirs() -> List[Path]:
    if not DATA_ROOT.exists():
        return []
    return sorted(p for p in DATA_ROOT.iterdir() if p.is_dir() and p.name.upper().startswith("CH"))


def _channel_sort_key(code: str) -> tuple[int, str]:
    upper = code.upper()
    match = re.match(r"^CH(\d+)$", upper)
    if not match:
        return (10**9, upper)
    return (int(match.group(1)), upper)


def list_known_channel_codes(channel_info_map: Optional[Dict[str, dict]] = None) -> List[str]:
    """
    Return a stable list of known channel codes.

    UI should be able to show channels even when `workspaces/scripts/CHxx/` is missing.
    Sources (union):
    - `workspaces/planning/channels/CHxx.csv` (Planning SoT)
    - `packages/script_pipeline/channels/CHxx-*/` (channel profiles)
    - `workspaces/scripts/CHxx/` (existing script data)
    - `channel_info_map` keys (already loaded from channels_info.json / channel_info.json)
    """

    codes: set[str] = set()

    if channel_info_map:
        codes.update(code.upper() for code in channel_info_map.keys())

    if CHANNEL_PLANNING_DIR.exists():
        for csv_path in CHANNEL_PLANNING_DIR.glob("CH*.csv"):
            codes.add(csv_path.stem.upper())

    for channel_dir in list_channel_dirs():
        codes.add(channel_dir.name.upper())

    if CHANNELS_DIR.exists():
        for child in CHANNELS_DIR.iterdir():
            if not child.is_dir():
                continue
            code = child.name.split("-", 1)[0].upper()
            if code:
                codes.add(code)

    filtered = [code for code in codes if re.match(r"^CH\d+$", code)]
    return sorted(filtered, key=_channel_sort_key)


def list_video_dirs(channel_code: str) -> List[Path]:
    channel_dir = DATA_ROOT / channel_code
    if not channel_dir.exists():
        return []
    return sorted((p for p in channel_dir.iterdir() if p.is_dir() and p.name.isdigit()), key=lambda p: int(p.name))


def list_planning_video_numbers(channel_code: str) -> List[str]:
    """
    Return normalized video numbers from Planning SoT (`workspaces/planning/channels/CHxx.csv`).

    Notes:
    - This intentionally does not depend on `planning_store.CHANNELS_DIR` so tests can monkeypatch
      `CHANNEL_PLANNING_DIR` safely.
    - Non-numeric video numbers are ignored (best-effort: digit extraction).
    """

    channel_code = channel_code.upper()
    csv_path = CHANNEL_PLANNING_DIR / f"{channel_code}.csv"
    if not csv_path.exists():
        return []

    numbers: List[str] = []
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if not isinstance(row, dict):
                    continue
                token = normalize_planning_video_number(row.get("動画番号") or row.get("VideoNumber") or "")
                if token:
                    numbers.append(token)
    except Exception:
        return []

    seen: set[str] = set()
    unique: List[str] = []
    for number in numbers:
        if number in seen:
            continue
        seen.add(number)
        unique.append(number)
    return unique


def initialize_stage_payload(initial_stage: Optional[str] = None) -> Dict[str, dict]:
    stages: Dict[str, dict] = {}
    encountered = False
    for stage in STAGE_ORDER:
        status = "pending"
        if initial_stage:
            if stage == initial_stage:
                status = "in_progress"
                encountered = True
            elif not encountered:
                status = "completed"
            else:
                status = "pending"
        stages[stage] = {"status": status}
    return stages


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


def _load_sections_from_text(text: str) -> List[Any]:
    """Helper that reuses iterate_sections by writing to a temp file."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not normalized:
        return []
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as tmp_file:
            tmp_file.write(normalized)
            tmp_file.flush()
            tmp_path = Path(tmp_file.name)
        sections = list(iterate_sections(tmp_path))
    finally:
        if tmp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                tmp_path.unlink()
    return sections


def _compose_tagged_tts(plain_text: str, silence_plan: Optional[Sequence[float]], pause_map: Optional[Sequence[Dict[str, Any]]]) -> str:
    sections = _load_sections_from_text(plain_text)
    if not sections:
        return plain_text

    total = len(sections)
    plan: List[float] = [0.0] * total
    if silence_plan:
        for idx in range(min(total, len(silence_plan))):
            try:
                plan[idx] = float(silence_plan[idx])
            except (TypeError, ValueError):
                plan[idx] = 0.0
    if pause_map:
        for entry in pause_map:
            try:
                section_idx = int(entry.get("section") or entry.get("section_index"))
            except (TypeError, ValueError):
                continue
            if not (1 <= section_idx <= total):
                continue
            try:
                plan[section_idx - 1] = float(entry.get("pause_sec") or 0.0)
            except (TypeError, ValueError):
                continue

    output_lines: List[str] = []
    for idx, section in enumerate(sections):
        output_lines.extend(section.lines)
        pause_value = plan[idx] if idx < len(plan) else 0.0
        if pause_value and pause_value > 0:
            output_lines.append(f"[{pause_value:.2f}s]")
        if idx < len(sections) - 1:
            output_lines.append("")
    return "\n".join(output_lines).strip()


def _parse_tagged_tts(tagged_text: str) -> Tuple[str, List[Dict[str, Any]], int]:
    sections = _load_sections_from_text(tagged_text)
    pause_entries: List[Dict[str, Any]] = []
    for section in sections:
        clean_lines, tags = pause_tags.strip_pause_tags_from_lines(section.lines)
        if tags:
            pause_sec = pause_tags.extract_last_pause_seconds(tags)
            if pause_sec is not None:
                pause_entries.append(
                    {
                        "section": section.index,
                        "pause_sec": round(float(pause_sec), 4),
                        "source": "user_tag",
                        "raw_tag": tags[-1].raw,
                    }
                )
    plain_text = pause_tags.PAUSE_TAG_PATTERN.sub("", tagged_text)
    plain_text = re.sub(r"\n{3,}", "\n\n", plain_text).strip()
    return plain_text, pause_entries, len(sections)


def analyze_tts_content(raw: str) -> Tuple[str, List[TTSIssue]]:
    normalized_input = raw.replace("\r\n", "\n").replace("\r", "\n")
    issues: List[TTSIssue] = []
    sanitized_lines: List[str] = []
    for idx, line in enumerate(normalized_input.splitlines(), 1):
        stripped = line.strip()
        cleaned_line, _ = pause_tags.remove_pause_tags(stripped)
        sanitized_lines.append(cleaned_line)
        if not cleaned_line:
            continue
        ascii_letters = sum(ch.isalpha() and ch.isascii() for ch in cleaned_line)
        ratio = ascii_letters / len(cleaned_line) if cleaned_line else 0.0
        if ratio >= 0.3:
            issues.append(
                TTSIssue(
                    type="non_japanese_ratio",
                    line=idx,
                    detail=cleaned_line[:80],
                )
            )
        if len(cleaned_line) > 120:
            issues.append(
                TTSIssue(
                    type="line_too_long",
                    line=idx,
                    detail=str(len(cleaned_line)),
                )
            )
        if any(token in cleaned_line for token in ("(", ")", "[", "]", "{", "}", "<", ">", "（", "）", "［", "］")):
            issues.append(
                TTSIssue(
                    type="bracket_detected",
                    line=idx,
                    detail=cleaned_line[:80],
                )
            )
    sans_tags_text = "\n".join(sanitized_lines)
    sanitized = ContentProcessor.sanitize_for_tts(sans_tags_text)
    return sanitized, issues


SRT_TIMESTAMP_PATTERN = re.compile(
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2}),(?P<millis>\d{3})"
)


def _parse_srt_timestamp(value: str) -> float:
    match = SRT_TIMESTAMP_PATTERN.match(value.strip())
    if not match:
        raise ValueError(f"Invalid timestamp: {value}")
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))
    millis = int(match.group("millis"))
    return hour * 3600 + minute * 60 + second + millis / 1000.0


def _iter_srt_blocks(path: Path) -> Iterable[Tuple[int, float, float]]:
    with path.open("r", encoding="utf-8") as handle:
        block: List[str] = []
        for line in handle:
            line = line.rstrip("\n")
            if line:
                block.append(line)
                continue
            if block:
                yield _parse_block(block)
                block = []
        if block:
            yield _parse_block(block)


def _parse_block(lines: List[str]) -> Tuple[int, float, float]:
    index = int(lines[0].strip())
    start_raw, end_raw = lines[1].split("-->")
    start = _parse_srt_timestamp(start_raw)
    end = _parse_srt_timestamp(end_raw)
    if end < start:
        raise ValueError(f"SRT block {index}: end < start ({start} -> {end})")
    return index, start, end


def verify_srt_file(
    wav_path: Path,
    srt_path: Path,
    *,
    tolerance_ms: int,
) -> SRTVerifyResponse:
    issues: List[SRTIssue] = []
    valid = True
    try:
        audio_duration = wav_tools.duration_from_file(wav_path)
    except Exception as exc:  # pragma: no cover - propagate error info
        issues.append(SRTIssue(type="audio_error", detail=str(exc)))
        return SRTVerifyResponse(
            valid=False,
            audio_duration_seconds=None,
            srt_duration_seconds=None,
            diff_ms=None,
            issues=issues,
        )

    last_end = 0.0
    previous_end = 0.0
    block_count = 0
    try:
        for index, start, end in _iter_srt_blocks(srt_path):
            block_count += 1
            if start < previous_end:
                issues.append(
                    SRTIssue(
                        type="overlap",
                        detail=f"Block {index} overlaps previous end {previous_end:.3f}s",
                        block=index,
                        start=start,
                        end=end,
                    )
                )
                valid = False
            previous_end = end
            last_end = max(last_end, end)
    except ValueError as exc:
        issues.append(SRTIssue(type="parse_error", detail=str(exc)))
        return SRTVerifyResponse(
            valid=False,
            audio_duration_seconds=audio_duration,
            srt_duration_seconds=None,
            diff_ms=None,
            issues=issues,
        )

    srt_duration = last_end
    diff_ms = abs(audio_duration - srt_duration) * 1000.0
    if diff_ms > tolerance_ms:
        issues.append(
            SRTIssue(
                type="duration_mismatch",
                detail=f"diff={diff_ms:.1f}ms exceeds tolerance {tolerance_ms}ms",
            )
        )
        valid = False

    if block_count == 0:
        issues.append(SRTIssue(type="empty_srt", detail="SRT file contains no blocks"))
        valid = False

    return SRTVerifyResponse(
        valid=valid,
        audio_duration_seconds=audio_duration,
        srt_duration_seconds=srt_duration,
        diff_ms=diff_ms,
        issues=issues,
    )


def _default_status_payload(channel_code: str, video_number: str) -> dict:
    return {
        "script_id": f"{channel_code}-{video_number}",
        "channel": channel_code,
        "status": "pending",
        "metadata": {},
        "stages": {stage: {"status": "pending", "details": {}} for stage in STAGE_ORDER},
    }


def load_or_init_status(channel_code: str, video_number: str) -> dict:
    status = load_status_optional(channel_code, video_number)
    if status is not None:
        return status

    payload = _default_status_payload(channel_code, video_number)
    # Best-effort: bootstrap title from planning CSV (if available).
    try:
        for row in planning_store.get_rows(channel_code, force_refresh=True):
            if not row.video_number:
                continue
            if normalize_video_number(row.video_number) != video_number:
                continue
            title = row.raw.get("タイトル") if isinstance(row.raw, dict) else None
            if isinstance(title, str) and title.strip():
                meta = payload.setdefault("metadata", {})
                meta.setdefault("sheet_title", title.strip())
                meta.setdefault("title", title.strip())
                meta.setdefault("expected_title", title.strip())
            break
    except Exception:
        pass

    save_status(channel_code, video_number, payload)
    return payload


def ensure_expected_updated_at(status: dict, expected: Optional[str]) -> None:
    """Compare the provided version token with the latest status and raise 409 if it diverges."""

    if expected is None:
        return
    current = status.get("updated_at")
    if current != expected:
        raise HTTPException(
            status_code=409,
            detail="他のセッションで更新されました。最新の情報を再取得してからもう一度保存してください。",
        )


def save_status(channel_code: str, video_number: str, payload: dict) -> None:
    status_path = DATA_ROOT / channel_code / video_number / "status.json"
    write_json(status_path, payload)
    # 同じ script_id の processing_status.json も同期する
    if PROGRESS_STATUS_PATH.exists():
        progress = load_json(PROGRESS_STATUS_PATH)
        status_script_id = payload.get("script_id")
        if progress.get("script_id") == status_script_id:
            # 特定のフィールドのみ更新
            progress.update({
                "status": payload.get("status"),
                "stages": payload.get("stages", {}),
                "metadata": payload.get("metadata", {}),
                "updated_at": payload.get("updated_at"),
                "completed_at": payload.get("completed_at"),
            })
            write_json(PROGRESS_STATUS_PATH, progress)


def run_ssot_sync_for_channel(channel_code: str, video_number: str) -> None:
    """
    Guard SoT after UI mutations.

    NOTE:
    - 外部スクリプト依存はしない（SoT は直接読み取る）。深い整合検査は `scripts/ops/planning_lint.py` を使用する。
    - ここでは「正本ファイルが存在し、最低限読める」ことだけを同期ガードとして検証する。
    """

    channel_code = normalize_channel_code(channel_code)
    video_number = normalize_video_number(video_number)

    csv_path = CHANNEL_PLANNING_DIR / f"{channel_code}.csv"
    status_json_path = DATA_ROOT / channel_code / video_number / "status.json"

    issues: list[str] = []
    if not csv_path.exists():
        issues.append("missing_planning_csv")
    if not status_json_path.exists():
        issues.append("missing_status_json")

    row_exists = False
    if csv_path.exists():
        try:
            import csv as _csv

            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                reader = _csv.DictReader(handle)
                for row in reader:
                    raw = row.get("動画番号") or row.get("video") or row.get("Video") or ""
                    if not raw:
                        continue
                    try:
                        token = normalize_video_number(raw)
                    except Exception:
                        continue
                    if token == video_number:
                        row_exists = True
                        break
        except Exception:
            issues.append("planning_csv_unreadable")

    if csv_path.exists() and not row_exists:
        issues.append("missing_planning_row")

    if status_json_path.exists():
        try:
            st = load_status(channel_code, video_number)
            if not isinstance(st, dict):
                issues.append("status_json_invalid_type")
        except Exception:
            issues.append("status_json_unreadable")

    if not issues:
        logger.info("SSOT guard ok for %s-%s", channel_code, video_number)
        return

    SSOT_SYNC_LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = current_timestamp_compact()
    log_path = SSOT_SYNC_LOG_DIR / f"ssot_guard_failure_{channel_code}_{video_number}_{timestamp}.json"
    log_payload = {
        "channel_code": channel_code,
        "video_number": video_number,
        "issues": issues,
        "planning_csv": str(safe_relative_path(csv_path) or csv_path),
        "status_json": str(safe_relative_path(status_json_path) or status_json_path),
    }
    log_path.write_text(json.dumps(log_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.error("SSOT guard failed for %s-%s: %s (log: %s)", channel_code, video_number, issues, log_path)
    raise HTTPException(
        status_code=502,
        detail="SSOTガードに失敗しました。ログを確認してから再試行してください。",
    )


def build_status_payload(
    *,
    channel_code: str,
    video_number: str,
    script_id: Optional[str],
    title: Optional[str],
    initial_stage: Optional[str],
    status_value: Optional[str],
    metadata_patch: Dict[str, Any],
    generation: Optional[VideoGenerationInfo],
    files: Optional[VideoFileReferences],
) -> dict:
    timestamp = current_timestamp()
    payload: Dict[str, Any] = {
        "script_id": script_id or f"{channel_code}-{video_number}",
        "channel": channel_code,
        "status": status_value or "pending",
        "stages": initialize_stage_payload(initial_stage),
        "created_at": timestamp,
        "updated_at": timestamp,
        "metadata": {},
    }
    metadata: Dict[str, Any] = {}
    if title:
        metadata["title"] = title
        metadata.setdefault("sheet_title", title)
    if generation:
        metadata["generation"] = generation.model_dump(exclude_none=True)
    if metadata_patch:
        metadata.update(metadata_patch)
    metadata.setdefault("ready_for_audio", False)

    files_dict = files.model_dump(exclude_none=True) if files else {}
    if files_dict.get("assembled"):
        metadata.setdefault("script", {})
        metadata["script"]["assembled_path"] = normalize_audio_path_string(files_dict["assembled"])
    if files_dict.get("tts"):
        audio_meta = metadata.setdefault("audio", {})
        prepare_meta = audio_meta.setdefault("prepare", {})
        prepare_meta["script_sanitized_path"] = normalize_audio_path_string(files_dict["tts"])

    payload["metadata"] = metadata
    return payload


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _normalize_status_token(value: Any) -> str:
    """
    Normalize various runner/UI status tokens into the small set the UI can reason about.

    The UI (frontend) expects stage statuses to collapse into:
      pending | in_progress | review | blocked | completed
    while older/legacy status.json may contain tokens like:
      processing | running | failed | skipped | done | ok ...
    """

    token = str(value or "").strip().lower()
    if not token or token == "pending":
        return "pending"
    if token in {"completed", "done", "ok", "success", "succeeded", "skipped"}:
        return "completed"
    if token in {"blocked", "failed", "error"}:
        return "blocked"
    if token in {"review"}:
        return "review"
    if token in {"in_progress", "processing", "running", "rerun_in_progress", "rerun_requested"}:
        return "in_progress"
    return "unknown"


def _stage_status_value(stage_entry: Any) -> str:
    if stage_entry is None:
        return "pending"
    raw = stage_entry.get("status") if isinstance(stage_entry, dict) else stage_entry
    normalized = _normalize_status_token(raw)
    if normalized in VALID_STAGE_STATUSES:
        return normalized
    return "unknown"


SCRIPT_DUMMY_MARKERS = (
    "この動画の台本本文は外部管理です",
    "ダミー本文を配置しています",
)

SCRIPT_ASSEMBLED_MILESTONE_STAGES = (
    # Script pipeline (current / new)
    "topic_research",
    "script_outline",
    "script_master_plan",
    "chapter_brief",
    "script_draft",
    "script_review",
    # Legacy / compatibility
    "script_enhancement",
    "quality_check",
)

SCRIPT_POST_VALIDATION_AUTOCOMPLETE_STAGES = (
    # Some pipelines don't emit these stages in status.json (UI treats missing as pending),
    # but they are effectively "skipped" once validation is completed.
    "script_polish_ai",
    "script_audio_ai",
    "script_tts_prepare",
)


def _ensure_stage_slot(stages: Dict[str, Any], key: str) -> Dict[str, Any]:
    slot = stages.get(key)
    if not isinstance(slot, dict):
        slot = {}
        stages[key] = slot
    slot.setdefault("status", "pending")
    return slot


def _inject_audio_completion_from_artifacts(
    channel_code: str, video_number: str, stages: Dict[str, Any], metadata: Dict[str, Any]
) -> Tuple[Dict[str, Any], bool, bool]:
    """
    既存の status/stages に音声・字幕の完成を反映する（ファイルが存在する場合）。
    永続化はせずレスポンス上で補正する。
    """
    stages_copy = copy.deepcopy(stages) if isinstance(stages, dict) else {}
    audio_stage = _ensure_stage_slot(stages_copy, "audio_synthesis")
    srt_stage = _ensure_stage_slot(stages_copy, "srt_generation")

    audio_meta = metadata.get("audio", {}) if isinstance(metadata, dict) else {}
    synth_meta = audio_meta.get("synthesis", {}) if isinstance(audio_meta, dict) else {}
    final_wav = synth_meta.get("final_wav") if isinstance(synth_meta, dict) else None
    if final_wav:
        audio_path = Path(final_wav)
        if not audio_path.is_absolute():
            audio_path = (PROJECT_ROOT / final_wav).resolve()
    else:
        audio_path = _detect_artifact_path(channel_code, video_number, ".wav")

    srt_meta = metadata.get("subtitles", {}) if isinstance(metadata, dict) else {}
    final_srt = srt_meta.get("final_srt") if isinstance(srt_meta, dict) else None
    if final_srt:
        srt_path = Path(final_srt)
        if not srt_path.is_absolute():
            srt_path = (PROJECT_ROOT / final_srt).resolve()
    else:
        srt_path = _detect_artifact_path(channel_code, video_number, ".srt")

    audio_exists = audio_path.exists() if audio_path else False
    srt_exists = srt_path.exists() if srt_path else False

    if audio_exists:
        audio_stage["status"] = "completed"
    if srt_exists:
        srt_stage["status"] = "completed"

    return stages_copy, audio_exists, srt_exists


def _resolve_script_artifact_candidates(
    *,
    base_dir: Path,
    metadata: Dict[str, Any],
) -> List[Path]:
    candidates: List[Path] = []

    # status.json may carry an explicit assembled_path (best-effort).
    assembled_path = None
    if isinstance(metadata, dict):
        assembled_path = metadata.get("assembled_path")
        script_meta = metadata.get("script") if assembled_path is None else None
        if assembled_path is None and isinstance(script_meta, dict):
            assembled_path = script_meta.get("assembled_path")
    if isinstance(assembled_path, str) and assembled_path.strip():
        try:
            p = Path(assembled_path)
            if not p.is_absolute():
                p = (PROJECT_ROOT / assembled_path).resolve()
            candidates.append(p)
        except Exception:
            pass

    candidates.extend(
        [
            base_dir / "content" / "assembled_human.md",
            base_dir / "content" / "assembled.md",
            # Legacy (backward-compat only; should not be reintroduced as canonical).
            base_dir / "content" / "final" / "assembled.md",
        ]
    )
    return candidates


def _a_text_file_ok(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        if path.stat().st_size <= 0:
            return False
    except Exception:
        return False
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            head = handle.read(4096)
    except Exception:
        return False
    return not any(marker in head for marker in SCRIPT_DUMMY_MARKERS)


def _detect_script_a_text(
    *,
    base_dir: Path,
    metadata: Dict[str, Any],
) -> Optional[Path]:
    for candidate in _resolve_script_artifact_candidates(base_dir=base_dir, metadata=metadata):
        if _a_text_file_ok(candidate):
            return candidate
    return None


def _inject_script_completion_from_artifacts(
    channel_code: str,
    video_number: str,
    stages: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Tuple[Dict[str, Any], bool]:
    """
    Derive script-stage progress from durable artifacts (A-text).

    Rationale:
    - The UI treats missing stage keys as "pending".
    - Stale/mixed status.json can claim early stages are pending even when assembled.md exists.
    - For progress display, "assembled present" is the most reliable milestone.

    This is a read-time override; it does NOT persist status.json.
    """
    stages_copy = copy.deepcopy(stages) if isinstance(stages, dict) else {}
    base_dir = video_base_dir(channel_code, video_number)
    a_text_path = _detect_script_a_text(base_dir=base_dir, metadata=metadata)
    assembled_ok = a_text_path is not None
    if not assembled_ok:
        return stages_copy, False

    for key in SCRIPT_ASSEMBLED_MILESTONE_STAGES:
        slot = _ensure_stage_slot(stages_copy, key)
        if _stage_status_value(slot) != "completed":
            slot["status"] = "completed"

    script_validation_done = _stage_status_value(stages_copy.get("script_validation")) == "completed"
    if script_validation_done:
        for key in SCRIPT_POST_VALIDATION_AUTOCOMPLETE_STAGES:
            slot = _ensure_stage_slot(stages_copy, key)
            if _stage_status_value(slot) != "completed":
                slot["status"] = "completed"

    return stages_copy, True


def _derive_effective_stages(
    *,
    channel_code: str,
    video_number: str,
    stages: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Tuple[Dict[str, Any], bool, bool, bool]:
    stages_effective, audio_exists, srt_exists = _inject_audio_completion_from_artifacts(
        channel_code, video_number, stages, metadata
    )
    stages_effective, a_text_ok = _inject_script_completion_from_artifacts(
        channel_code, video_number, stages_effective, metadata
    )
    return stages_effective, a_text_ok, audio_exists, srt_exists


def _derive_effective_video_status(
    *,
    raw_status: str,
    stages: Dict[str, Any],
    a_text_ok: bool,
    audio_exists: bool,
    srt_exists: bool,
    published_locked: bool = False,
) -> str:
    raw = str(raw_status or "").strip().lower()
    if published_locked:
        return "completed"
    if audio_exists and srt_exists:
        return "completed"

    if _stage_status_value(stages.get("script_validation")) == "completed" or raw == "script_validated":
        return "script_validated"
    if raw in {"script_ready", "script_completed"}:
        return "script_ready"
    if a_text_ok:
        return "script_ready"

    # Fallback: collapse into pending / in_progress / blocked / review based on stage states.
    any_blocked = any(_stage_status_value(stages.get(stage_key)) == "blocked" for stage_key in STAGE_ORDER)
    if any_blocked or str(raw_status or "").strip().lower() == "blocked":
        return "blocked"

    any_started = any(_stage_status_value(stage_entry) != "pending" for stage_entry in (stages or {}).values())
    return "in_progress" if any_started else "pending"


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


def _extract_script_summary(channel_code: str, video_number: str) -> Optional[str]:
    """Assembled台本の冒頭から、説明文用の短い要約を作る。"""
    base_dir = video_base_dir(channel_code, video_number)
    candidates = [
        base_dir / "content" / "assembled_human.md",
        base_dir / "content" / "assembled.md",
    ]
    for path in candidates:
        try:
            if path.exists() and path.is_file():
                raw_text = path.read_text(encoding="utf-8")
                if not raw_text:
                    continue
                text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
                paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
                paragraphs = [p for p in paragraphs if p.strip() != "---"]
                if not paragraphs:
                    continue

                def is_title_like(paragraph: str) -> bool:
                    candidate = paragraph.strip()
                    if "\n" in candidate:
                        return False
                    if len(candidate) > 30:
                        return False
                    if any(ch in candidate for ch in ("、", "！", "？", "!", "?", "「", "」")):
                        return False
                    return candidate.endswith("。") or candidate.endswith("…") or bool(re.match(r"^[#\s]+$", candidate))

                body: List[str] = []
                for paragraph in paragraphs:
                    if not body and is_title_like(paragraph):
                        continue
                    body.append(paragraph)
                    if len(body) >= 3 or sum(len(p) for p in body) >= 260:
                        break
                if not body:
                    body = paragraphs[:1]
                block = "\n".join(body).strip()
                if not block:
                    continue
                # 文の先頭2〜3文を抜粋
                sentences = [s for s in block.replace("！", "。").replace("？", "。").split("。") if s.strip()]
                summary = "。".join(sentences[:3]).strip()
                return (summary + "。").strip() if summary else block[:200]
        except Exception:
            continue
    return None


def _normalize_description_length(text: str, *, max_len: int = 900) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    # Prefer cutting at a block boundary first (copy-friendly).
    cut = text.rfind("\n", 0, max_len)
    if cut >= int(max_len * 0.6):
        return text[:cut].rstrip() + "\n…"
    # Fallback: cut by Japanese sentence boundary.
    sentences = [s for s in text.split("。") if s.strip()]
    trimmed = ""
    for s in sentences:
        candidate = (trimmed + s + "。").strip()
        if len(candidate) > max_len:
            break
        trimmed = candidate
    if trimmed:
        return trimmed + "…"
    return text[: max_len - 1].rstrip() + "…"


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_youtube_description_text(text: Optional[str]) -> Optional[str]:
    if not isinstance(text, str):
        return None
    value = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    value = _ANSI_ESCAPE_RE.sub("", value)
    value = _CONTROL_CHARS_RE.sub("", value)
    value = value.replace("\ufffd", "")  # Unicode replacement char (mojibake marker)
    # Normalize excessive blank lines (copy-friendly).
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value or None


def _normalize_description_field(text: Optional[str]) -> Optional[str]:
    value = _sanitize_youtube_description_text(text)
    if not value:
        return None
    # Planning fields sometimes contain HTML line breaks for UI; normalize to plain text.
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = value.replace("&nbsp;", " ")
    # Best-effort HTML tag removal (avoid copy/paste artifacts).
    value = re.sub(r"</?[^>]+>", "", value)
    return value.strip() or None


def _build_bullet_list(text: Optional[str]) -> Optional[str]:
    value = _normalize_description_field(text)
    if not value:
        return None
    raw_lines = [line.strip() for line in value.splitlines() if line.strip()]
    lines = [line.lstrip("・").lstrip("-").lstrip("•").strip() for line in raw_lines]
    lines = [line for line in lines if line]
    if not lines:
        return None
    return "・" + "\n・".join(lines)


def _get_channel_profile(channel_code: str) -> Dict[str, Any]:
    info_map = refresh_channel_info()
    info = info_map.get((channel_code or "").upper(), {})
    return info if isinstance(info, dict) else {}


def _channel_subscribe_url(channel_info: Dict[str, Any]) -> Optional[str]:
    if not isinstance(channel_info, dict):
        return None
    # Prefer handle/custom URL for copy friendliness; fall back to channel URL.
    youtube_meta = channel_info.get("youtube")
    if isinstance(youtube_meta, dict):
        handle = youtube_meta.get("handle") or youtube_meta.get("custom_url") or channel_info.get("youtube_handle")
        if isinstance(handle, str) and handle.strip():
            handle = handle.strip()
            if handle.startswith("@"):
                return f"https://www.youtube.com/{handle}"
            return handle
        url = youtube_meta.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    handle = channel_info.get("youtube_handle")
    if isinstance(handle, str) and handle.strip():
        handle = handle.strip()
        if handle.startswith("@"):
            return f"https://www.youtube.com/{handle}"
        return handle
    return None


def _voice_credit_line(channel_info: Dict[str, Any]) -> Optional[str]:
    prod = channel_info.get("production_sources") if isinstance(channel_info, dict) else None
    voice_config_path = prod.get("voice_config_path") if isinstance(prod, dict) else None
    if not isinstance(voice_config_path, str) or not voice_config_path.strip():
        return None
    try:
        voice_cfg = load_json(PROJECT_ROOT / voice_config_path)
    except Exception:
        return None
    if not isinstance(voice_cfg, dict):
        return None
    default_key = voice_cfg.get("default_voice_key")
    voices = voice_cfg.get("voices")
    if not isinstance(default_key, str) or not isinstance(voices, dict):
        return None
    voice = voices.get(default_key, {})
    if not isinstance(voice, dict):
        return None
    character = voice.get("character")
    engine = voice.get("engine")
    if not isinstance(character, str) or not character.strip():
        return None
    character = character.strip()
    if str(engine).lower() == "voicevox":
        return f"VOICEVOX:{character}"
    return f"音声:{character}"


def _hashtags_line(*tags: Optional[str], max_tags: int = 12) -> Optional[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in tags:
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value:
            continue
        value = value.lstrip("#").strip()
        if not value or any(ch.isspace() for ch in value):
            continue
        tag = f"#{value}"
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= max_tags:
            break
    return " ".join(out) if out else None


def _build_youtube_description(channel_code: str, video_number: str, metadata: Dict[str, Any], title: Optional[str]) -> str:
    """Generate a richer YouTube description from planning + 台本本文。"""

    planning = metadata.get("planning", {}) if isinstance(metadata, dict) else {}

    def pget(key: str) -> Optional[str]:
        value = planning.get(key) if isinstance(planning, dict) else None
        if not value and isinstance(metadata, dict):
            value = metadata.get(key)
        if isinstance(value, str):
            value = value.strip()
        return value or None

    channel_code = (channel_code or "").upper()

    lead = _normalize_description_field(pget("description_lead"))
    takeaways = _normalize_description_field(pget("description_takeaways"))
    audience = pget("target_audience")
    main_tag = pget("primary_pain_tag")
    sub_tag = pget("secondary_pain_tag")
    life_scene = pget("life_scene")

    title_text = title or pget("sheet_title") or pget("title") or ""

    takeaways_block = _build_bullet_list(takeaways)

    script_summary = _extract_script_summary(channel_code, video_number)
    summary_line = _normalize_description_field(script_summary) or (lead if lead and "フィクション" not in lead else None)

    def fmt(blocks: List[Optional[str]], *, max_len: int = 4500) -> str:
        text = "\n\n".join(filter(None, blocks))
        text = _sanitize_youtube_description_text(text) or ""
        return _normalize_description_length(text, max_len=max_len)

    channel_info = _get_channel_profile(channel_code)
    subscribe_url = _channel_subscribe_url(channel_info)
    subscribe_block = f"🔔チャンネル登録はこちら\n{subscribe_url}" if subscribe_url else None
    voice_line = _voice_credit_line(channel_info)

    # CH22: senior friendship/community story channel (benchmark-aligned, copy-friendly)
    if channel_code == "CH22":
        takeaways_section = f"▼この動画でわかること\n{takeaways_block}" if takeaways_block else None
        teaser = (
            summary_line
            or _normalize_description_field(pget("content_summary"))
            or (f"今日の物語：{title_text}" if title_text else None)
            or "老後の友人関係を、物語で整える回です。"
        )
        question = (
            "皆さんは、友人関係で「この人とは合わないかも」と感じた経験はありますか？\n"
            "もし同じような経験や、人間関係で気をつけていることがあれば、ぜひコメント欄で教えてください。"
        )
        fiction = (
            "この物語はフィクションです。\n"
            "登場する人物・団体・名称等は架空であり、実在のものとは関係ありません。"
        )
        hashtags = _hashtags_line(
            "老後",
            "朗読",
            "シニア",
            "友人関係",
            "人間関係",
            life_scene,
            main_tag,
            sub_tag,
        )
        return fmt([teaser, takeaways_section, question, subscribe_block, fiction, hashtags, voice_line])

    if channel_code in {"CH01", "CH07", "CH11"}:
        opener = f"この動画では「{title_text}」を仏教の視点でやさしく解き明かします。"
        body = summary_line or "心が折れそうなときに使える“たった一言”をお届け。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：一呼吸おいて距離をとる / 優しさと境界線を両立する"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#仏教 #心を整える #人間関係"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH02", "CH10"}:
        opener = f"{title_text} を哲学・心理と偉人の言葉で分解し、静かな思考法に落とし込みます。"
        body = summary_line or "考えすぎる夜に“考えない時間”をつくるための小さなステップを紹介。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：深呼吸・メモ・短い無思考タイムを挟む"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#思考法 #哲学 #夜のラジオ"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH04"}:
        opener = f"{title_text} の“違和感/謎”を心理・脳科学・物語で探究し、日常に使える視点に翻訳します。"
        body = summary_line or "静かな語りで“なるほど”を届ける知的エンタメ回です。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：気づいた違和感をメモし、1日1つ観察してみる"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#心理学 #脳科学 #好奇心 #知的エンタメ"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH03"}:
        opener = f"{title_text} を“病院任せにしない”日常習慣で整える方法をまとめました。"
        body = summary_line or "50〜70代の体と心をやさしくケアするシンプルなステップ。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：寝る前の呼吸・短いストレッチ・水分補給"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#シニア健康 #習慣化 #ウェルネス"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH05"}:
        opener = f"{title_text} を安心とユーモアで解説。距離の取り方・伝え方・再出発のヒントを紹介。"
        body = summary_line or "シニア世代の恋愛・パートナーシップを穏やかに進めるための道しるべ。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：短い挨拶・連絡頻度の合意・1つの共通体験を増やす"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#シニア恋愛 #コミュ力 #第二の人生"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH06"}:
        opener = f"{title_text} の“噂”と“根拠”を切り分け、考察で本当かもしれないを探ります。"
        body = summary_line or "ワクワクしつつ冷静に検証する安全運転の都市伝説ガイド。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：複数ソースを照合・仮説と事実を分けてメモ"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#都市伝説 #考察 #検証"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH08"}:
        opener = f"{title_text} を“悪用厳禁”の視点で安全に扱う方法を解説します。"
        body = summary_line or "波動・カルマ・反応しない力を、心理とミニ実験付きで紹介。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"⚠️ 注意：\n{takeaways_block}" if takeaways_block else "⚠️ 注意：無理をせず、体調や人間関係を優先して試してください。"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#スピリチュアル #波動 #自己浄化"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH09"}:
        opener = f"{title_text} を“危険人物/言ってはいけない言葉”の視点で整理し、線引きのチェックリストを提供。"
        body = summary_line or "舐められない距離感と、今日からできる自己防衛の一言。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：言わないリストを作る / 距離を置くサインを1つ決める"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#人間関係 #自己防衛 #線引き"
        return fmt([opener, body, audience_line, take_line, hash_line])

    # Common fallback (all channels): dynamic header + channel template as footer.
    template = _normalize_description_field(
        channel_info.get("youtube_description") if isinstance(channel_info, dict) else None
    )
    takeaways_section = f"▼この動画でわかること\n{takeaways_block}" if takeaways_block else None
    hash_line = _hashtags_line(main_tag, sub_tag, life_scene)
    return fmt(
        [
            f"{title_text} の要点を短くまとめました。" if title_text else None,
            summary_line,
            takeaways_section,
            subscribe_block,
            template,
            hash_line,
            voice_line,
        ]
    )


def _fallback_character_count_from_files(
    metadata: Dict[str, Any], channel_code: str, video_number: str
) -> Optional[int]:
    """
    Fallback: count characters from assembled files when metadata is missing or zero.
    """
    candidates: List[Path] = []
    assembled_path = metadata.get("assembled_path")
    script_meta = metadata.get("script")
    if not assembled_path and isinstance(script_meta, dict):
        assembled_path = script_meta.get("assembled_path")
    if assembled_path:
        path = Path(assembled_path)
        if not path.is_absolute():
            path = (PROJECT_ROOT / assembled_path).resolve()
        candidates.append(path)
    base_dir = video_base_dir(channel_code, video_number)
    candidates.append(base_dir / "content" / "assembled.md")
    candidates.append(base_dir / "content" / "assembled_human.md")

    for path in candidates:
        try:
            if path.exists() and path.is_file():
                text = path.read_text(encoding="utf-8")
                if text:
                    return len(text)
        except Exception:
            continue
    return None


def _character_count_from_a_text(channel_code: str, video_number: str) -> Optional[int]:
    """
    Prefer accurate count by reading the current Aテキスト (assembled_human/assembled → audio_prep/script_sanitized).
    """
    try:
        path = _resolve_a_text_display_path(channel_code, video_number)
    except HTTPException:
        return None
    try:
        text = path.read_text(encoding="utf-8")
        # Match UI display semantics: count without line breaks.
        return len(text.replace("\r", "").replace("\n", ""))
    except Exception:
        return None

def replace_text(content: str, original: str, replacement: str, scope: str) -> Tuple[str, int]:
    if scope == "all":
        count = content.count(original)
        if count == 0:
            return content, 0
        return content.replace(original, replacement), count
    index = content.find(original)
    if index == -1:
        return content, 0
    return content.replace(original, replacement, 1), 1


def update_tts_metadata(status: dict, plain_path: Path, tagged_path: Optional[Path], timestamp: str) -> None:
    metadata = status.setdefault("metadata", {})
    audio_meta = metadata.setdefault("audio", {})
    prepare_meta = audio_meta.setdefault("prepare", {})
    prepare_meta["script_sanitized_path"] = safe_relative_path(plain_path) or str(plain_path)
    if tagged_path is not None:
        prepare_meta["script_tagged_path"] = safe_relative_path(tagged_path) or str(tagged_path)
    else:
        prepare_meta.pop("script_tagged_path", None)
    prepare_meta["updated_at"] = timestamp


def _persist_tts_variants(
    base_dir: Path,
    status: dict,
    tagged_content: str,
    *,
    timestamp: str,
    update_assembled: bool = False,
) -> Tuple[str, List[Dict[str, Any]]]:
    plain_content, pause_map, section_count = _parse_tagged_tts(tagged_content)

    audio_prep_dir = base_dir / "audio_prep"
    audio_prep_dir.mkdir(parents=True, exist_ok=True)

    plain_path = audio_prep_dir / "script_sanitized.txt"
    tagged_path = audio_prep_dir / "script_sanitized_with_pauses.txt"

    # 正規パスガード（フォールバック禁止）
    if plain_path.parent.name != "audio_prep":
        raise HTTPException(status_code=400, detail="invalid tts path")
    if tagged_path.parent.name != "audio_prep":
        raise HTTPException(status_code=400, detail="invalid tts_tagged path")

    write_text_with_lock(tagged_path, tagged_content)
    write_text_with_lock(plain_path, plain_content)

    if update_assembled:
        content_dir = base_dir / "content"
        assembled_path = content_dir / "assembled.md"
        assembled_human_path = content_dir / "assembled_human.md"
        if assembled_path.parent.name != "content":
            raise HTTPException(status_code=400, detail="invalid assembled path")
        if assembled_human_path.parent.name != "content":
            raise HTTPException(status_code=400, detail="invalid assembled_human path")
        target = assembled_human_path if assembled_human_path.exists() else assembled_path
        try:
            write_text_with_lock(target, plain_content)
            if target != assembled_path:
                write_text_with_lock(assembled_path, plain_content)
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - unexpected file errors
            logger.exception("Failed to update assembled.md for %s", base_dir)
            raise HTTPException(status_code=500, detail=f"assembled.md の更新に失敗しました: {exc}")

    metadata = status.setdefault("metadata", {})
    audio_meta = metadata.setdefault("audio", {})
    if pause_map:
        audio_meta["pause_map"] = pause_map
    else:
        audio_meta.pop("pause_map", None)

    synthesis_meta = audio_meta.setdefault("synthesis", {})
    existing_plan = synthesis_meta.get("silence_plan") if isinstance(synthesis_meta.get("silence_plan"), list) else []
    plan: List[float] = list(existing_plan) if isinstance(existing_plan, list) else []
    if section_count and len(plan) < section_count:
        plan.extend([0.0] * (section_count - len(plan)))
    if not plan and section_count:
        plan = [0.0] * section_count
    for entry in pause_map:
        section_idx = entry.get("section")
        pause_value = entry.get("pause_sec")
        if isinstance(section_idx, int) and isinstance(pause_value, (int, float)) and 1 <= section_idx <= len(plan):
            plan[section_idx - 1] = float(pause_value)
    if plan:
        synthesis_meta["silence_plan"] = plan

    update_tts_metadata(status, plain_path, tagged_path, timestamp)

    return plain_content, pause_map


def append_audio_history_entry(channel_code: str, video_number: str, entry: Dict[str, Any]) -> None:
    status_path = DATA_ROOT / channel_code / video_number / "status.json"
    if not status_path.exists():
        return
    try:
        payload = load_json(status_path)
    except HTTPException:
        return
    timestamp = entry.get("timestamp") or current_timestamp()
    history_entry = dict(entry)
    history_entry["timestamp"] = timestamp
    metadata = payload.setdefault("metadata", {})
    audio_meta = metadata.setdefault("audio", {})
    history = audio_meta.setdefault("history", [])
    history.append(history_entry)
    if len(history) > 50:
        del history[:-50]
    write_json(status_path, payload)


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


def summarize_log(log_path: Path) -> Optional[dict]:
    if not log_path or not log_path.exists():
        return None
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    meta = data.get("audio") or {}
    engine = data.get("engine") or meta.get("engine")
    duration = meta.get("duration_sec")
    chunk_meta = data.get("engine_metadata", {}).get("chunk_meta")
    chunk_count = len(chunk_meta) if isinstance(chunk_meta, list) else None
    return {
        "engine": engine,
        "duration_sec": duration,
        "chunk_count": chunk_count,
    }


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


class StageStatus(BaseModel):
    status: str = Field("pending")

    @field_validator("status")
    @classmethod
    def validate_stage_status(cls, value: str) -> str:
        if value not in VALID_STAGE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid stage status: {value!r}",
            )
        return value


class StageUpdateRequest(OptimisticUpdateRequest):
    stages: Dict[str, StageStatus]


class TtsUpdateRequest(OptimisticUpdateRequest):
    content: Optional[str] = Field(None, description="ポーズタグを含まないプレーンテキスト（レガシー互換）")
    tagged_content: Optional[str] = Field(
        None, description="ポーズタグ付きテキスト（[0.5s] などのタグを含む場合はこちらを指定）"
    )
    content_mode: Optional[Literal["plain", "tagged"]] = Field(
        None, description="どちらのテキストを編集したか。未指定の場合は自動推定します。"
    )
    regenerate_audio: Optional[bool] = Field(None, description="音声と字幕を再生成するか")
    update_assembled: Optional[bool] = Field(None, description="assembled.md も同期更新するか")

    @model_validator(mode="after")
    def _validate_payload(self) -> "TtsUpdateRequest":
        if self.content is None and self.tagged_content is None:
            raise HTTPException(status_code=400, detail="content または tagged_content を指定してください。")
        return self


class ReadyUpdateRequest(OptimisticUpdateRequest):
    ready: bool


class StatusUpdateRequest(OptimisticUpdateRequest):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="status は空にできません。")
        if len(normalized) > MAX_STATUS_LENGTH:
            raise HTTPException(status_code=400, detail="status が長すぎます。64文字以内にしてください。")
        return normalized


class VideoGenerationInfo(BaseModel):
    mode: Optional[str] = Field(None, description="auto / interactive などのモード表記")
    prompt_version: Optional[str] = None
    logs: Optional[str] = Field(None, description="生成時のログパス")


class VideoFileReferences(BaseModel):
    assembled: Optional[str] = Field(None, description="assembled.md の格納パス")
    tts: Optional[str] = Field(None, description="script_sanitized.txt の格納パス")


class VideoCreateRequest(BaseModel):
    video: str = Field(..., description="動画番号（数字）")
    script_id: Optional[str] = Field(None, description="スクリプトID")
    title: Optional[str] = Field(None, description="タイトル")
    generation: Optional[VideoGenerationInfo] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    initial_stage: Optional[str] = Field(None, description="着手済みステージ")
    status: Optional[str] = Field(None, description="全体ステータス", max_length=MAX_STATUS_LENGTH)
    files: Optional[VideoFileReferences] = None

    @field_validator("video")
    @classmethod
    def validate_video(cls, value: str) -> str:
        raw = value.strip()
        if not raw.isdigit():
            raise HTTPException(status_code=400, detail="video は数字のみ指定してください。")
        return raw

    @field_validator("initial_stage")
    @classmethod
    def validate_initial_stage(cls, value: Optional[str]) -> Optional[str]:
        if value and value not in STAGE_ORDER:
            raise HTTPException(status_code=400, detail=f"未知のステージ: {value}")
        return value

    @field_validator("status")
    @classmethod
    def validate_initial_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip()
        if len(normalized) > MAX_STATUS_LENGTH:
            raise HTTPException(status_code=400, detail="status が長すぎます。64文字以内にしてください。")
        if not normalized:
            raise HTTPException(status_code=400, detail="status は空にできません。")
        return normalized


class TtsReplaceRequest(OptimisticUpdateRequest):
    original: str = Field(..., description="置換対象の文字列")
    replacement: str = Field(..., description="置換後の文字列")
    scope: Optional[str] = Field("first", description="first または all")
    update_assembled: bool = Field(False, description="assembled.md も同時に置換するか")
    regenerate_audio: bool = Field(True, description="音声とSRTを再生成するか")

    @field_validator("original")
    @classmethod
    def validate_original(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="original は必須です。")
        return normalized

    @field_validator("replacement")
    @classmethod
    def validate_replacement(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="replacement は必須です。")
        return normalized

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: Optional[str]) -> str:
        allowed = {"first", "all"}
        scope = (value or "first").lower()
        if scope not in allowed:
            raise HTTPException(status_code=400, detail=f"scope は {allowed} のいずれかを指定してください。")
        return scope


class TtsReplaceResponse(BaseModel):
    replaced: int
    content: str
    plain_content: str
    tagged_content: Optional[str] = None
    pause_map: Optional[List[Dict[str, Any]]] = None
    audio_regenerated: bool
    message: Optional[str] = None


class ArtifactEntryResponse(BaseModel):
    key: str
    label: str
    path: str
    kind: Literal["file", "dir"] = "file"
    exists: bool
    size_bytes: Optional[int] = None
    modified_time: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


class ArtifactsSummaryResponse(BaseModel):
    project_dir: Optional[str] = None
    items: List[ArtifactEntryResponse] = Field(default_factory=list)


class VideoDetailResponse(BaseModel):
    channel: str
    video: str
    script_id: Optional[str]
    title: Optional[str]
    status: str
    ready_for_audio: bool
    stages: Dict[str, str]
    stage_details: Optional[Dict[str, Any]] = None
    redo_script: bool = True
    redo_audio: bool = True
    redo_note: Optional[str] = None
    alignment_status: Optional[str] = None
    alignment_reason: Optional[str] = None
    assembled_path: Optional[str]
    assembled_content: Optional[str]
    assembled_human_path: Optional[str] = None
    assembled_human_content: Optional[str] = None
    tts_path: Optional[str]
    tts_content: Optional[str]
    tts_plain_content: Optional[str] = None
    tts_tagged_path: Optional[str] = None
    tts_tagged_content: Optional[str] = None
    script_audio_path: Optional[str] = None
    script_audio_content: Optional[str] = None
    script_audio_human_path: Optional[str] = None
    script_audio_human_content: Optional[str] = None
    srt_path: Optional[str]
    srt_content: Optional[str]
    audio_path: Optional[str]
    audio_url: Optional[str]
    audio_duration_seconds: Optional[float] = None
    audio_updated_at: Optional[str] = None
    audio_quality_status: Optional[str] = None
    audio_quality_summary: Optional[str] = None
    audio_quality_report: Optional[str] = None
    audio_metadata: Optional[Dict[str, Any]] = None
    tts_pause_map: Optional[List[Dict[str, Any]]] = None
    audio_reviewed: Optional[bool] = False
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    ui_session_token: Optional[str] = None
    planning: Optional[PlanningInfoResponse] = None
    youtube_description: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    artifacts: Optional[ArtifactsSummaryResponse] = None


class AudioReviewItemResponse(BaseModel):
    channel: str
    video: str
    status: str
    title: Optional[str] = None
    channel_title: Optional[str] = None
    workspace_path: str
    audio_stage: str
    audio_stage_updated_at: Optional[str] = None
    subtitle_stage: str
    subtitle_stage_updated_at: Optional[str] = None
    audio_quality_status: Optional[str] = None
    audio_quality_summary: Optional[str] = None
    audio_updated_at: Optional[str] = None
    audio_duration_seconds: Optional[float] = None
    audio_url: Optional[str] = None
    audio_waveform_image: Optional[str] = None
    audio_waveform_url: Optional[str] = None
    audio_message: Optional[str] = None
    audio_error: Optional[str] = None
    manual_pause_count: Optional[int] = None
    ready_for_audio: bool = False
    tts_input_path: Optional[str] = None
    audio_log_url: Optional[str] = None
    audio_engine: Optional[str] = None
    audio_log_summary: Optional[dict] = None


class TTSIssue(BaseModel):
    type: str
    line: Optional[int] = None
    detail: Optional[str] = None


class TTSValidateRequest(BaseModel):
    content: str


class TTSValidateResponse(BaseModel):
    sanitized_content: str
    issues: List[TTSIssue]
    valid: bool


class ThumbnailProgressResponse(BaseModel):
    created: bool = False
    created_at: Optional[str] = None
    qc_cleared: bool = False
    qc_cleared_at: Optional[str] = None
    status: Optional[str] = None
    variant_count: int = 0


class VideoImagesProgressResponse(BaseModel):
    run_id: Optional[str] = None
    prompt_ready: bool = False
    prompt_ready_at: Optional[str] = None
    cue_count: Optional[int] = None
    prompt_count: Optional[int] = None
    images_count: int = 0
    images_complete: bool = False
    images_updated_at: Optional[str] = None


class VideoSummaryResponse(BaseModel):
    video: str
    script_id: Optional[str]
    title: Optional[str]
    status: str
    ready_for_audio: bool
    published_lock: bool = False
    stages: Dict[str, str]
    updated_at: Optional[str] = None
    character_count: int = 0
    a_text_exists: bool = False
    a_text_character_count: int = 0
    planning_character_count: Optional[int] = None
    planning: Optional[PlanningInfoResponse] = None
    youtube_description: Optional[str] = None
    thumbnail_progress: Optional[ThumbnailProgressResponse] = None
    video_images_progress: Optional[VideoImagesProgressResponse] = None

class ThumbnailVariantResponse(BaseModel):
    id: str
    label: Optional[str] = None
    status: Optional[str] = None
    image_url: Optional[str] = None
    image_path: Optional[str] = None
    preview_url: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    model_key: Optional[str] = None
    openrouter_generation_id: Optional[str] = None
    cost_usd: Optional[float] = None
    usage: Optional[Dict[str, Any]] = None
    is_selected: Optional[bool] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ThumbnailProjectResponse(BaseModel):
    channel: str
    video: str
    script_id: Optional[str] = None
    title: Optional[str] = None
    sheet_title: Optional[str] = None
    status: Optional[str] = None
    owner: Optional[str] = None
    summary: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    variants: List[ThumbnailVariantResponse]
    ready_for_publish: Optional[bool] = None
    updated_at: Optional[str] = None
    status_updated_at: Optional[str] = None
    due_at: Optional[str] = None
    selected_variant_id: Optional[str] = None
    audio_stage: Optional[str] = None
    script_stage: Optional[str] = None


class ThumbnailChannelVideoResponse(BaseModel):
    video_id: str
    title: str
    url: str
    thumbnail_url: Optional[str] = None
    published_at: Optional[str] = None
    view_count: Optional[int] = None
    duration_seconds: Optional[float] = None
    estimated_ctr: Optional[float] = None
    source: Literal["youtube", "variant"] = "youtube"


class ThumbnailChannelSummaryResponse(BaseModel):
    total: int
    subscriber_count: Optional[int] = None
    view_count: Optional[int] = None
    video_count: Optional[int] = None


class ThumbnailChannelBlockResponse(BaseModel):
    channel: str
    channel_title: Optional[str] = None
    summary: ThumbnailChannelSummaryResponse
    projects: List[ThumbnailProjectResponse]
    videos: List[ThumbnailChannelVideoResponse]
    library_path: Optional[str] = None


class ThumbnailOverviewResponse(BaseModel):
    generated_at: Optional[str] = None
    channels: List[ThumbnailChannelBlockResponse]


class ThumbnailLibraryAssetResponse(BaseModel):
    id: str
    file_name: str
    size_bytes: int
    updated_at: str
    public_url: str
    relative_path: str


class ThumbnailQuickHistoryEntry(BaseModel):
    channel: str
    video: str
    label: Optional[str] = None
    asset_name: str
    image_path: Optional[str] = None
    public_url: str
    timestamp: str


class ThumbnailLibraryRenameRequest(BaseModel):
    new_name: str

    @field_validator("new_name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("invalid name")
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("empty name")
        if "/" in trimmed or "\\" in trimmed:
            raise ValueError("name must not contain path separators")
        suffix = Path(trimmed).suffix.lower()
        if suffix not in THUMBNAIL_SUPPORTED_EXTENSIONS:
            raise ValueError(f"拡張子は {', '.join(sorted(THUMBNAIL_SUPPORTED_EXTENSIONS))} のいずれかにしてください。")
        return trimmed


class ThumbnailLibraryAssignRequest(BaseModel):
    video: str
    label: Optional[str] = None
    make_selected: Optional[bool] = None

    @field_validator("video")
    @classmethod
    def _validate_video(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("invalid video identifier")
        text = value.strip()
        if not text:
            raise ValueError("video identifier is required")
        if not text.isdigit():
            raise ValueError("動画番号は数字で入力してください。")
        return text


class ThumbnailLibraryAssignResponse(BaseModel):
    file_name: str
    image_path: str
    public_url: str


class ThumbnailAssetReplaceResponse(BaseModel):
    status: str
    channel: str
    video: str
    slot: str
    file_name: str
    image_path: str
    public_url: str


class ThumbnailProjectUpdateRequest(BaseModel):
    owner: Optional[str] = Field(default=None)
    summary: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    tags: Optional[List[str]] = Field(default=None)
    due_at: Optional[str] = Field(default=None)
    status: Optional[str] = Field(default=None)
    selected_variant_id: Optional[str] = Field(default=None)


class ThumbnailVariantCreateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=160)
    status: Optional[str] = Field(default="draft")
    image_url: Optional[str] = None
    image_path: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    prompt: Optional[str] = None
    make_selected: Optional[bool] = False

    @model_validator(mode="after")
    def _ensure_source(self):
        if not (self.image_url or self.image_path):
            raise ValueError("画像URLまたは画像パスを指定してください。")
        return self


class ThumbnailVariantGenerateRequest(BaseModel):
    template_id: Optional[str] = None
    image_model_key: Optional[str] = None
    prompt: Optional[str] = None
    count: int = Field(default=1, ge=1, le=4)
    label: Optional[str] = None
    status: Optional[str] = Field(default="draft")
    make_selected: Optional[bool] = False
    notes: Optional[str] = None
    tags: Optional[List[str]] = None


class ThumbnailVariantComposeRequest(BaseModel):
    """
    Local composition (no AI): put 3-line text on the fixed Buddha template.
    """

    copy_upper: Optional[str] = None
    copy_title: Optional[str] = None
    copy_lower: Optional[str] = None
    label: Optional[str] = None
    status: Optional[str] = Field(default="draft")
    make_selected: Optional[bool] = False
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    impact: Optional[bool] = True
    flip_base: Optional[bool] = True


class ThumbnailLibraryImportRequest(BaseModel):
    url: str = Field(..., min_length=1)
    file_name: Optional[str] = None

class ThumbnailDescriptionResponse(BaseModel):
    description: str
    model: Optional[str] = None
    source: Literal["openai", "openrouter", "heuristic"]


class ThumbnailTemplatePayload(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=160)
    image_model_key: str = Field(..., min_length=1, max_length=160)
    prompt_template: str = Field(..., min_length=1)
    negative_prompt: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _normalize_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("invalid template id")
        trimmed = value.strip()
        return trimmed or None

    @field_validator("image_model_key")
    @classmethod
    def _normalize_model_key(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("invalid model key")
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("model key is required")
        return trimmed


class ThumbnailTemplateResponse(BaseModel):
    id: str
    name: str
    image_model_key: str
    prompt_template: str
    negative_prompt: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ThumbnailChannelStyleResponse(BaseModel):
    name: Optional[str] = None
    benchmark_path: Optional[str] = None
    preview_upper: Optional[str] = None
    preview_title: Optional[str] = None
    preview_lower: Optional[str] = None
    rules: Optional[List[str]] = None


class ThumbnailChannelTemplatesResponse(BaseModel):
    channel: str
    default_template_id: Optional[str] = None
    templates: List[ThumbnailTemplateResponse]
    channel_style: Optional[ThumbnailChannelStyleResponse] = None


class ThumbnailChannelTemplatesUpdateRequest(BaseModel):
    default_template_id: Optional[str] = None
    templates: List[ThumbnailTemplatePayload] = Field(default_factory=list)


class ThumbnailLayerSpecRefResponse(BaseModel):
    id: str
    kind: str
    version: int
    path: str
    name: Optional[str] = None


class ThumbnailChannelLayerSpecsResponse(BaseModel):
    channel: str
    image_prompts: Optional[ThumbnailLayerSpecRefResponse] = None
    text_layout: Optional[ThumbnailLayerSpecRefResponse] = None


class ThumbnailLayerSpecPlanningSuggestionsResponse(BaseModel):
    thumbnail_prompt: Optional[str] = None
    thumbnail_upper: Optional[str] = None
    thumbnail_title: Optional[str] = None
    thumbnail_lower: Optional[str] = None
    text_design_note: Optional[str] = None


class ThumbnailVideoTextLayoutSpecResponse(BaseModel):
    template_id: Optional[str] = None
    fallbacks: Optional[List[str]] = None
    text: Optional[Dict[str, str]] = None


class ThumbnailVideoLayerSpecsResponse(BaseModel):
    channel: str
    video: str
    video_id: str
    image_prompt: Optional[str] = None
    text_layout: Optional[ThumbnailVideoTextLayoutSpecResponse] = None
    planning_suggestions: Optional[ThumbnailLayerSpecPlanningSuggestionsResponse] = None


class ThumbnailImageModelInfoResponse(BaseModel):
    key: str
    provider: str
    model_name: str
    pricing: Optional[Dict[str, str]] = None
    pricing_updated_at: Optional[str] = None


class ThumbnailParamCatalogEntryResponse(BaseModel):
    path: str
    kind: str
    engine: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None


class ThumbnailThumbSpecUpdateRequest(BaseModel):
    overrides: Dict[str, Any] = Field(default_factory=dict)


class ThumbnailThumbSpecResponse(BaseModel):
    exists: bool
    path: Optional[str] = None
    schema: Optional[str] = None
    channel: str
    video: str
    overrides: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[str] = None
    normalized_overrides_leaf: Dict[str, Any] = Field(default_factory=dict)

THUMBNAIL_TEXT_LINE_SPEC_SCHEMA_V1 = "ytm.thumbnail.text_line_spec.v1"


class ThumbnailTextLineSpecLinePayload(BaseModel):
    offset_x: float = 0.0
    offset_y: float = 0.0
    scale: float = 1.0
    rotate_deg: float = 0.0


class ThumbnailTextLineSpecUpdateRequest(BaseModel):
    lines: Dict[str, ThumbnailTextLineSpecLinePayload] = Field(default_factory=dict)


class ThumbnailTextLineSpecResponse(BaseModel):
    exists: bool
    path: Optional[str] = None
    schema: str = THUMBNAIL_TEXT_LINE_SPEC_SCHEMA_V1
    channel: str
    video: str
    stable: str
    lines: Dict[str, ThumbnailTextLineSpecLinePayload] = Field(default_factory=dict)
    updated_at: Optional[str] = None


THUMBNAIL_ELEMENTS_SPEC_SCHEMA_V1 = "ytm.thumbnail.elements_spec.v1"


class ThumbnailElementStrokePayload(BaseModel):
    color: Optional[str] = None
    width_px: float = 0.0


class ThumbnailElementPayload(BaseModel):
    id: str
    kind: str
    layer: str = "above_portrait"  # above_portrait | below_portrait
    z: int = 0
    x: float = 0.5  # normalized center (0-1), can go out of frame
    y: float = 0.5
    w: float = 0.2  # normalized size (relative to canvas)
    h: float = 0.2
    rotation_deg: float = 0.0
    opacity: float = 1.0
    fill: Optional[str] = None
    stroke: Optional[ThumbnailElementStrokePayload] = None
    src_path: Optional[str] = None  # relative path under workspaces/thumbnails/assets (e.g. CHxx/library/foo.png)


class ThumbnailElementsSpecUpdateRequest(BaseModel):
    elements: List[ThumbnailElementPayload] = Field(default_factory=list)


class ThumbnailElementsSpecResponse(BaseModel):
    exists: bool
    path: Optional[str] = None
    schema: str = THUMBNAIL_ELEMENTS_SPEC_SCHEMA_V1
    channel: str
    video: str
    stable: str
    elements: List[ThumbnailElementPayload] = Field(default_factory=list)
    updated_at: Optional[str] = None


class ThumbnailPreviewTextSlotImageResponse(BaseModel):
    image_url: str
    image_path: str


class ThumbnailPreviewTextLayerSlotsResponse(BaseModel):
    status: str
    channel: str
    video: str
    template_id: Optional[str] = None
    images: Dict[str, ThumbnailPreviewTextSlotImageResponse] = Field(default_factory=dict)


class ThumbnailPreviewTextLayerSlotsRequest(BaseModel):
    overrides: Dict[str, Any] = Field(default_factory=dict)
    # Optional Canva-like per-line tuning (currently uses `scale` only; offsets are applied client-side).
    lines: Dict[str, ThumbnailTextLineSpecLinePayload] = Field(default_factory=dict)


class ThumbnailVariantPatchRequest(BaseModel):
    label: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    make_selected: Optional[bool] = None


class ThumbnailTwoUpBuildResponse(BaseModel):
    status: str
    channel: str
    video: str
    outputs: Dict[str, str] = Field(default_factory=dict)
    paths: Dict[str, str] = Field(default_factory=dict)


class ThumbnailLayerSpecsBuildRequest(BaseModel):
    allow_generate: bool = False
    regen_bg: bool = False
    output_mode: Literal["draft", "final"] = "draft"


class ThumbnailLayerSpecsBuildResponse(BaseModel):
    status: str
    channel: str
    video: str
    build_id: str
    thumb_url: str
    thumb_path: str
    build_meta_path: Optional[str] = None


class ThumbnailPreviewTextLayerResponse(BaseModel):
    status: str
    channel: str
    video: str
    image_url: str
    image_path: str


class ThumbnailTextSlotMetaResponse(BaseModel):
    box: Optional[List[float]] = None
    fill: Optional[str] = None
    base_size_px: Optional[int] = None
    align: Optional[str] = None
    valign: Optional[str] = None


class ThumbnailTextTemplateOptionResponse(BaseModel):
    id: str
    description: Optional[str] = None
    slots: Dict[str, ThumbnailTextSlotMetaResponse] = Field(default_factory=dict)


class ThumbnailEditorContextResponse(BaseModel):
    channel: str
    video: str
    video_id: str
    portrait_available: bool = False
    portrait_dest_box_norm: Optional[List[float]] = None
    portrait_anchor: Optional[str] = None
    template_id_default: Optional[str] = None
    template_options: List[ThumbnailTextTemplateOptionResponse] = Field(default_factory=list)
    text_slots: Dict[str, str] = Field(default_factory=dict)
    defaults_leaf: Dict[str, Any] = Field(default_factory=dict)
    overrides_leaf: Dict[str, Any] = Field(default_factory=dict)
    effective_leaf: Dict[str, Any] = Field(default_factory=dict)

THUMBNAIL_COMMENT_PATCH_SCHEMA_V1 = "ytm.thumbnail.comment_patch.v1"


class ThumbnailCommentPatchTargetResponse(BaseModel):
    channel: str
    video: str


class ThumbnailCommentPatchOpResponse(BaseModel):
    op: Literal["set", "unset"] = "set"
    path: str
    value: Optional[Any] = None
    reason: Optional[str] = None


class ThumbnailCommentPatchResponse(BaseModel):
    schema: str = THUMBNAIL_COMMENT_PATCH_SCHEMA_V1
    target: ThumbnailCommentPatchTargetResponse
    confidence: float = 0.0
    clarifying_questions: List[str] = Field(default_factory=list)
    ops: List[ThumbnailCommentPatchOpResponse] = Field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None


class ThumbnailCommentPatchRequest(BaseModel):
    comment: str
    include_thumb_caption: bool = False


def _coerce_video_from_dir(name: str) -> Optional[str]:
    if not name:
        return None
    match = re.match(r"(\d+)", name.strip())
    if not match:
        return None
    return match.group(1).zfill(3)


def _thumbnail_asset_roots(channel_code: str) -> List[Path]:
    # Canonical root: workspaces/thumbnails/assets/{CH}/
    # (Do not scan package channel dirs; avoid legacy multi-root ambiguity.)
    return [THUMBNAIL_ASSETS_DIR / channel_code]


def _collect_disk_thumbnail_variants(channel_code: str) -> Dict[str, List[ThumbnailVariantResponse]]:
    variant_map: Dict[str, List[ThumbnailVariantResponse]] = {}
    seen_paths: set[str] = set()
    for root in _thumbnail_asset_roots(channel_code):
        if not root.exists():
            continue
        for video_dir in root.iterdir():
            if not video_dir.is_dir():
                continue
            video_number = _coerce_video_from_dir(video_dir.name)
            if not video_number:
                continue
            for asset_path in sorted(video_dir.rglob("*")):
                if not asset_path.is_file():
                    continue
                suffix = asset_path.suffix.lower()
                if suffix not in THUMBNAIL_SUPPORTED_EXTENSIONS:
                    continue
                try:
                    rel_asset = asset_path.relative_to(video_dir)
                except ValueError:
                    rel_asset = Path(asset_path.name)
                public_rel = (Path(channel_code) / video_number / rel_asset).as_posix()
                if public_rel in seen_paths:
                    continue
                seen_paths.add(public_rel)
                label = rel_asset.as_posix()
                if suffix:
                    label = label[: -len(suffix)]
                label = label or asset_path.stem
                timestamp = datetime.fromtimestamp(asset_path.stat().st_mtime, timezone.utc).isoformat()
                digest = hashlib.sha1(public_rel.encode("utf-8")).hexdigest()[:12]
                asset_url = f"/thumbnails/assets/{public_rel}"
                variant = ThumbnailVariantResponse(
                    id=f"fs::{digest}",
                    label=label,
                    status="draft",
                    image_url=asset_url,
                    image_path=public_rel,
                    preview_url=asset_url,
                    notes=None,
                    tags=None,
                    is_selected=False,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                variant_map.setdefault(video_number, []).append(variant)
    return variant_map


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


def get_llm_settings():
    return _build_llm_settings_response()


def update_llm_settings(payload: LLMSettingsUpdate):
    settings = _get_ui_settings()
    updated = copy.deepcopy(settings.get("llm", {}))
    if payload.caption_provider:
        updated["caption_provider"] = payload.caption_provider
    if payload.openai_api_key is not None:
        cleaned = payload.openai_api_key.strip() if payload.openai_api_key else ""
        updated["openai_api_key"] = cleaned or None
    if payload.openrouter_api_key is not None:
        cleaned = payload.openrouter_api_key.strip() if payload.openrouter_api_key else ""
        updated["openrouter_api_key"] = cleaned or None
    if payload.openai_caption_model is not None:
        cleaned = payload.openai_caption_model.strip() or None
        if cleaned:
            validation_key = updated.get("openai_api_key") or os.getenv("OPENAI_API_KEY") or _load_env_value("OPENAI_API_KEY")
            if not validation_key:
                raise HTTPException(status_code=400, detail="OpenAI APIキーを先に設定してください。")
            try:
                models = _list_openai_model_ids(validation_key)
            except HTTPException as exc:
                raise HTTPException(status_code=400, detail=f"OpenAI モデル一覧取得に失敗しました: {exc.detail}") from exc
            if cleaned not in models:
                raise HTTPException(status_code=400, detail=f"OpenAIモデル {cleaned} は現在利用できません。")
        updated["openai_caption_model"] = cleaned
    if payload.openrouter_caption_model is not None:
        cleaned = payload.openrouter_caption_model.strip() or None
        if cleaned:
            validation_key = updated.get("openrouter_api_key") or os.getenv("OPENROUTER_API_KEY") or _load_env_value("OPENROUTER_API_KEY")
            if not validation_key:
                raise HTTPException(status_code=400, detail="OpenRouter APIキーを先に設定してください。")
            try:
                models = _list_openrouter_model_ids(validation_key)
            except HTTPException as exc:
                raise HTTPException(status_code=400, detail=f"OpenRouter モデル一覧取得に失敗しました: {exc.detail}") from exc
            if cleaned not in models:
                raise HTTPException(status_code=400, detail=f"OpenRouterモデル {cleaned} は現在利用できません。")
        updated["openrouter_caption_model"] = cleaned
    if payload.phase_models is not None and isinstance(payload.phase_models, dict):
        merged_phase_models: Dict[str, Dict[str, object]] = copy.deepcopy(updated.get("phase_models") or {})
        for phase_id, info in payload.phase_models.items():
            base = merged_phase_models.get(phase_id, {})
            merged_phase_models[phase_id] = {
                "label": (info.get("label") if isinstance(info, dict) else None) or base.get("label") or phase_id,
                "provider": (info.get("provider") if isinstance(info, dict) else None) or base.get("provider") or "openrouter",
                "model": (info.get("model") if isinstance(info, dict) else None) or base.get("model"),
            }
        # fail-fast: providerとエンドポイント/キーの整合性を検査
        for pid, info in merged_phase_models.items():
            prov = str(info.get("provider") or "").lower()
            if prov in {"openai", "openrouter", "gemini"}:
                _validate_provider_endpoint(prov)
        updated["phase_models"] = merged_phase_models
    new_settings = copy.deepcopy(settings)
    new_settings["llm"] = _normalize_llm_settings(updated)
    _write_ui_settings(new_settings)
    return _build_llm_settings_response()


def get_codex_settings():
    return _build_codex_settings_response()


def update_codex_settings(payload: CodexSettingsUpdate):
    with CODEX_SETTINGS_LOCK:
        # Update pipeline config (configs/codex_exec.local.yaml)
        exec_doc = _load_codex_exec_config_doc()
        current_profile = (
            (os.getenv("YTM_CODEX_EXEC_PROFILE") or "").strip()
            or str(exec_doc.get("profile") or "").strip()
            or "claude-code"
        )
        profile = payload.profile.strip() if isinstance(payload.profile, str) else current_profile
        patch: Dict[str, Any] = {}
        if payload.profile is not None:
            if not profile:
                raise HTTPException(status_code=400, detail="profile は必須です。")
            patch["profile"] = profile
        if payload.model is not None:
            patch["model"] = (payload.model or "").strip()
        if patch:
            _write_codex_exec_local_config(patch)

        # Update Codex CLI profile ( ~/.codex/config.toml )
        cli_profile = (
            (payload.cli_profile or "").strip()
            or profile
            or current_profile
            or "claude-code"
        )
        kvs: Dict[str, str] = {}
        if payload.model_reasoning_effort is not None:
            eff = str(payload.model_reasoning_effort).strip().lower()
            if eff not in _ALLOWED_CODEX_REASONING_EFFORT:
                raise HTTPException(status_code=400, detail=f"model_reasoning_effort は {', '.join(_ALLOWED_CODEX_REASONING_EFFORT)} のいずれかです。")
            kvs["model_reasoning_effort"] = eff
        if payload.cli_model is not None:
            model = str(payload.cli_model or "").strip()
            if model:
                kvs["model"] = model
        if kvs:
            if not CODEX_CONFIG_TOML_PATH.exists():
                raise HTTPException(status_code=404, detail=f"Codex設定が見つかりません: {CODEX_CONFIG_TOML_PATH}")
            try:
                original = CODEX_CONFIG_TOML_PATH.read_text(encoding="utf-8")
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Codex設定の読み込みに失敗しました: {exc}") from exc

            updated = _upsert_codex_profile_kv(original, profile=cli_profile, kvs=kvs)
            if updated != original:
                # Keep a single rolling backup (no SSOT noise; user-home only).
                try:
                    backup_path = CODEX_CONFIG_TOML_PATH.with_name(CODEX_CONFIG_TOML_PATH.name + ".bak")
                    backup_path.write_text(original, encoding="utf-8")
                except Exception:
                    pass
                try:
                    CODEX_CONFIG_TOML_PATH.write_text(updated, encoding="utf-8")
                except Exception as exc:
                    raise HTTPException(status_code=500, detail=f"Codex設定の書き込みに失敗しました: {exc}") from exc

    return _build_codex_settings_response()


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


def _load_video_channel_presets_document() -> tuple[Path, dict]:
    path = VIDEO_CHANNEL_PRESETS_PATH
    payload: dict
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse %s: %s. Recreating file.", path, exc)
            payload = {}
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    channels = payload.get("channels")
    if not isinstance(channels, dict):
        channels = {}
    payload["channels"] = channels
    return path, payload


def _write_video_channel_presets_document(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


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

    g_flash, g_flash_ok, g_flash_note = _mk_opt("gemini_2_5_flash_image", fallback_note="未設定", task="thumbnail_image_gen")
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
            raise HTTPException(status_code=400, detail=f"{label}: blocked resolved model_key: {resolved_key}")
        if model_index and resolved_key not in model_index:
            raise HTTPException(status_code=400, detail=f"{label}: slot resolves to unknown model_key: {resolved_key}")
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
    info = info_map.get(channel_code)
    if not isinstance(info, dict):
        return None
    branding = info.get("branding")
    if isinstance(branding, dict):
        title = branding.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    name = info.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    youtube_meta = info.get("youtube")
    if isinstance(youtube_meta, dict):
        yt_title = youtube_meta.get("title")
        if isinstance(yt_title, str) and yt_title.strip():
            return yt_title.strip()
    return None


def _resolve_thumbnail_projects_path() -> Path:
    for candidate in THUMBNAIL_PROJECTS_CANDIDATES:
        if candidate.exists():
            return candidate
    return THUMBNAIL_PROJECTS_CANDIDATES[0]


def _resolve_thumbnail_templates_path() -> Path:
    for candidate in THUMBNAIL_TEMPLATES_CANDIDATES:
        if candidate.exists():
            return candidate
    return THUMBNAIL_TEMPLATES_CANDIDATES[0]


def _load_thumbnail_projects_document() -> tuple[Path, dict]:
    path = _resolve_thumbnail_projects_path()
    payload: dict
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse %s: %s. Recreating file.", path, exc)
            payload = {}
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("version", 1)
    projects = payload.get("projects")
    if not isinstance(projects, list):
        projects = []
    payload["projects"] = projects
    return path, payload


def _write_thumbnail_projects_document(path: Path, payload: dict) -> None:
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def _load_thumbnail_templates_document() -> tuple[Path, dict]:
    path = _resolve_thumbnail_templates_path()
    payload: dict
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse %s: %s. Recreating file.", path, exc)
            payload = {}
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("version", 1)
    channels = payload.get("channels")
    if not isinstance(channels, dict):
        channels = {}
    payload["channels"] = channels
    return path, payload


def _write_thumbnail_templates_document(path: Path, payload: dict) -> None:
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


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


def _get_openrouter_pricing_by_model_id(
    *, max_age_sec: int = OPENROUTER_MODELS_CACHE_TTL_SEC, timeout_sec: int = 10
) -> Tuple[Dict[str, Dict[str, str]], float]:
    """
    Fetch OpenRouter model pricing table (best-effort) from `/api/v1/models`.

    Returns:
      - pricing_by_id: { model_id: { pricing_key: unit_price_str } }
      - fetched_at_epoch: seconds since epoch (UTC)
    """
    now = time.time()
    with OPENROUTER_MODELS_CACHE_LOCK:
        fetched_at = float(OPENROUTER_MODELS_CACHE.get("fetched_at") or 0.0)
        cached = OPENROUTER_MODELS_CACHE.get("pricing_by_id")
        if isinstance(cached, dict) and cached and (now - fetched_at) < max_age_sec:
            return cached, fetched_at

    try:
        resp = requests.get(OPENROUTER_MODELS_URL, timeout=timeout_sec)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        with OPENROUTER_MODELS_CACHE_LOCK:
            fetched_at = float(OPENROUTER_MODELS_CACHE.get("fetched_at") or 0.0)
            cached = OPENROUTER_MODELS_CACHE.get("pricing_by_id")
            if isinstance(cached, dict) and cached:
                return cached, fetched_at
        return {}, 0.0

    models = payload.get("data") if isinstance(payload, dict) else None
    pricing_by_id: Dict[str, Dict[str, str]] = {}
    if isinstance(models, list):
        for model in models:
            if not isinstance(model, dict):
                continue
            model_id = str(model.get("id") or "").strip()
            if not model_id:
                continue
            pricing = model.get("pricing")
            if not isinstance(pricing, dict):
                continue
            normalized: Dict[str, str] = {}
            for key, val in pricing.items():
                if val is None:
                    continue
                if isinstance(val, (int, float)):
                    normalized[str(key)] = str(val)
                elif isinstance(val, str):
                    normalized[str(key)] = val
            if normalized:
                pricing_by_id[model_id] = normalized

    with OPENROUTER_MODELS_CACHE_LOCK:
        OPENROUTER_MODELS_CACHE["fetched_at"] = now
        OPENROUTER_MODELS_CACHE["pricing_by_id"] = pricing_by_id

    return pricing_by_id, now


def _fetch_openrouter_generation(gen_id: str, *, timeout_sec: int = 10) -> Optional[Dict[str, Any]]:
    """
    Fetch OpenRouter generation metadata (includes billed cost) from `/api/v1/generation`.

    Docs: https://openrouter.ai/docs/api-reference/get-a-generation
    """
    if not gen_id:
        return None
    key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_TOKEN") or _load_env_value("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        resp = requests.get(
            OPENROUTER_GENERATION_URL,
            headers={"Authorization": f"Bearer {key}"},
            params={"id": gen_id},
            timeout=timeout_sec,
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            return payload["data"]
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


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


def _resolve_a_text_display_path(channel: str, video: str) -> Path:
    """
    Aテキスト（表示用）用に解決するパス。
    優先: content/assembled_human.md -> content/assembled.md
    """
    base = DATA_ROOT / channel / video
    candidates = [
        base / "content" / "assembled_human.md",
        base / "content" / "assembled.md",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise HTTPException(status_code=404, detail=f"A-text not found: {channel}-{video}")


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


def _build_codex_settings_response() -> CodexSettingsResponse:
    base_doc: Dict[str, Any] = {}
    local_doc: Dict[str, Any] = {}
    if CODEX_EXEC_CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(CODEX_EXEC_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                base_doc = raw
        except Exception:
            base_doc = {}
    if CODEX_EXEC_LOCAL_CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(CODEX_EXEC_LOCAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                local_doc = raw
        except Exception:
            local_doc = {}
    exec_doc = _deep_merge_dict(base_doc, local_doc)

    env_profile = (os.getenv("YTM_CODEX_EXEC_PROFILE") or "").strip()
    env_model = (os.getenv("YTM_CODEX_EXEC_MODEL") or "").strip()
    base_profile = str(base_doc.get("profile") or "").strip()
    local_profile = str(local_doc.get("profile") or "").strip()
    base_model = str(base_doc.get("model") or "").strip()
    local_model = str(local_doc.get("model") or "").strip()

    effective_profile = env_profile or local_profile or base_profile or "claude-code"
    effective_model = env_model or local_model or base_model or ""
    profile_source = "env" if env_profile else ("local" if local_profile else ("base" if base_profile else "default"))
    model_source = "env" if env_model else ("local" if local_model else ("base" if base_model else "default"))

    codex_exec = CodexExecConfig(
        profile=effective_profile,
        model=effective_model or None,
        sandbox=str(exec_doc.get("sandbox") or "").strip() or None,
        timeout_s=int(exec_doc.get("timeout_s") or 0) or None,
        profile_source=profile_source,
        model_source=model_source,
        local_config_path=str(CODEX_EXEC_LOCAL_CONFIG_PATH),
        base_config_path=str(CODEX_EXEC_CONFIG_PATH),
    )

    cli_exists = CODEX_CONFIG_TOML_PATH.exists()
    profiles: Dict[str, Dict[str, Optional[str]]] = {}
    if cli_exists:
        try:
            text = CODEX_CONFIG_TOML_PATH.read_text(encoding="utf-8")
            profiles = _parse_codex_profiles_from_toml(text)
        except Exception:
            profiles = {}
    cli_profiles = [
        CodexCliProfile(
            name=name,
            model=(conf.get("model") if isinstance(conf, dict) else None),
            model_reasoning_effort=(conf.get("model_reasoning_effort") if isinstance(conf, dict) else None),
        )
        for name, conf in sorted(profiles.items(), key=lambda kv: kv[0])
    ]
    cli = CodexCliConfig(
        config_path=str(CODEX_CONFIG_TOML_PATH),
        exists=cli_exists,
        profiles=cli_profiles,
    )

    active_conf = profiles.get(effective_profile, {}) if isinstance(profiles, dict) else {}
    active_profile = CodexCliProfile(
        name=effective_profile,
        model=(active_conf.get("model") if isinstance(active_conf, dict) else None),
        model_reasoning_effort=(active_conf.get("model_reasoning_effort") if isinstance(active_conf, dict) else None),
    )
    return CodexSettingsResponse(
        codex_exec=codex_exec,
        codex_cli=cli,
        active_profile=active_profile,
        allowed_reasoning_effort=list(_ALLOWED_CODEX_REASONING_EFFORT),
    )


def _build_llm_settings_response() -> LLMSettingsResponse:
    settings = _get_ui_settings()
    llm = settings.get("llm", {})
    openai_env_key = os.getenv("OPENAI_API_KEY") or _load_env_value("OPENAI_API_KEY")
    openrouter_env_key = OPENROUTER_API_KEY or os.getenv("OPENROUTER_API_KEY")
    openai_models: List[str] = []
    openrouter_models: List[str] = []
    openai_models_error: Optional[str] = None
    openrouter_models_error: Optional[str] = None
    try:
        effective_openai_key = llm.get("openai_api_key") or openai_env_key
        if effective_openai_key:
            openai_models = _list_openai_model_ids(effective_openai_key)
            openai_models = sorted(set(openai_models))
    except HTTPException as exc:
        logger.warning("Failed to load OpenAI model list: %s", exc.detail)
        openai_models_error = str(exc.detail)
    def _prioritize_models(model_ids: List[str]) -> List[str]:
        if not model_ids:
            return []
        try:
            from backend.app.llm_models import load_llm_model_scores

            curated = []
            seen = set()
            for model in load_llm_model_scores():
                mid = getattr(model, "model_id", None)
                if mid and mid in model_ids and mid not in seen:
                    curated.append(mid)
                    seen.add(mid)
            for mid in model_ids:
                if mid not in seen:
                    curated.append(mid)
                    seen.add(mid)
            return curated
        except Exception:
            return model_ids

    try:
        effective_openrouter_key = llm.get("openrouter_api_key") or openrouter_env_key
        if effective_openrouter_key:
            openrouter_models = _list_openrouter_model_ids(effective_openrouter_key)
            openrouter_models = _prioritize_models(sorted(set(openrouter_models)))
    except HTTPException as exc:
        logger.warning("Failed to load OpenRouter model list: %s", exc.detail)
        openrouter_models_error = str(exc.detail)
    def _mask_secret(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        trimmed = value.strip()
        if len(trimmed) <= 8:
            return trimmed[:2] + "***"
        return f"{trimmed[:4]}...{trimmed[-4:]}"

    def _export_phase_models() -> Dict[str, Dict[str, object]]:
        exported: Dict[str, Dict[str, object]] = {}
        phase_models = llm.get("phase_models") or {}
        for phase_id, info in phase_models.items():
            exported[phase_id] = {
                "label": info.get("label") or phase_id,
                "provider": info.get("provider") or "openrouter",
                "model": info.get("model"),
            }
        return exported

    def _export_phase_details() -> Dict[str, Dict[str, object]]:
        details: Dict[str, Dict[str, object]] = {}
        # 手動定義: 各フェーズの説明/パス/プロンプト出典
        details.update({
            "caption": {
                "label": "サムネキャプション",
                "role": "画像キャプション生成",
                "path": "apps/ui-backend/backend/main.py::_generate_thumbnail_caption",
                "prompt_source": "コード内 + configs/llm_router.yaml (tasks.visual_thumbnail_caption)",
                "endpoint": "LLMRouter (API) + THINK failover",
            },
            "script_rewrite": {
                "label": "台本リライト",
                "role": "台本セグメントのリライト",
                "prompt_source": "SYSTEM_PROMPT + build_user_prompt",
                "endpoint": "OpenAI(Azure)/OpenRouter",
            },
            "natural_command": {
                "label": "ナチュラルコマンド",
                "role": "自然言語コマンド解釈",
                "path": "apps/ui-backend/backend/main.py::_call_llm_for_command",
                "prompt_source": "コード内 + configs/llm_router.yaml (tasks.tts_natural_command)",
                "endpoint": "LLMRouter (API) + THINK failover",
            },
            "research": {
                "label": "リサーチ",
                "role": "情報収集・要約",
                "prompt_source": "メソッド内組み立て",
                "endpoint": "OpenAI(Azure)/OpenRouter",
            },
            "review": {
                "label": "レビュー",
                "role": "品質/論理性レビュー",
                "prompt_source": "メソッド内組み立て",
                "endpoint": "OpenAI(Azure)/OpenRouter",
            },
            "enhance": {
                "label": "エンハンス",
                "role": "文章強化・拡張",
                "prompt_source": "メソッド内組み立て",
                "endpoint": "OpenAI(Azure)/OpenRouter",
            },
            "script_polish_ai": {
                "label": "台本ポリッシュ",
                "role": "Stage8 ポリッシュ",
                "prompt_source": "packages/script_pipeline/prompts/llm_polish_template.txt + workspaces/planning/personas/{CH}_PERSONA.md",
                "endpoint": "OpenAI(Azure)優先 / OpenRouter fallback",
            },
            "audio_text": {
                "label": "音声テキスト生成(改行最適化)",
                "role": "27/54文字制約の改行最適化",
                "prompt_source": "メソッド内組み立て",
                "endpoint": "Gemini or OpenAI(Azure) if forced",
            },
            "image_generation": {
                "label": "画像生成",
                "role": "Gemini画像生成",
                "path": "packages/video_pipeline/src/srt2images/nanobanana_client.py::_run_direct",
                "prompt_source": "呼び出し元渡し（固定プロンプトなし）",
                "endpoint": "Gemini 2.5 Flash Image Preview",
            },
            "context_analysis": {
                "label": "文脈解析",
                "role": "SRTセクション分割",
                "path": "packages/video_pipeline/src/srt2images/llm_context_analyzer.py::LLMContextAnalyzer.analyze_story_sections",
                "prompt_source": "_create_analysis_prompt（動的生成）",
                "endpoint": "Gemini 2.5 Pro",
            },
        })
        # model/providerを phase_models から補完
        pm = llm.get("phase_models") or {}
        for pid, info in pm.items():
            det = details.setdefault(pid, {})
            det["provider"] = info.get("provider")
            det["model"] = info.get("model")
            det.setdefault("label", info.get("label") or pid)
        return details

    # ... (keep existing logic for calculating vars)
    
    # Build LLMConfig object
    config = LLMConfig(
        caption_provider=llm.get("caption_provider") or "openai",
        openai_api_key=llm.get("openai_api_key"),
        openai_caption_model=llm.get("openai_caption_model"),
        openrouter_api_key=llm.get("openrouter_api_key"),
        openrouter_caption_model=llm.get("openrouter_caption_model"),
        openai_key_configured=bool(llm.get("openai_api_key") or openai_env_key),
        openrouter_key_configured=bool(llm.get("openrouter_api_key") or openrouter_env_key),
        openai_models=openai_models,
        openrouter_models=openrouter_models,
        openai_key_preview=_mask_secret(llm.get("openai_api_key") or openai_env_key),
        openrouter_key_preview=_mask_secret(llm.get("openrouter_api_key") or openrouter_env_key),
        openai_models_error=openai_models_error,
        openrouter_models_error=openrouter_models_error,
        phase_models=_export_phase_models(),
        phase_details=_export_phase_details(),
    )
    return LLMSettingsResponse(llm=config)
