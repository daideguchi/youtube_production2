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
import wave
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
    OptimisticUpdateRequest,
    ScriptTextResponse,
    TextUpdateRequest,
)
from backend.app.youtube_client import YouTubeDataClient, YouTubeDataAPIError
from backend.app.normalize import normalize_channel_code, normalize_video_number
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

def _read_csv_file(path: Path) -> Tuple[List[str], List[List[str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            headers = next(reader)
        except StopIteration:
            return [], []
        rows = [row for row in reader]
    return headers, rows


def _ensure_planning_store_ready() -> None:
    if planning_store.list_channels():
        return
    detail = "channels CSV がまだ生成されていません。ssot_sync を実行してください。"
    raise HTTPException(status_code=503, detail=detail)


def _build_spreadsheet_from_planning(channel_code: str) -> PlanningSpreadsheetResponse:
    _ensure_planning_store_ready()
    rows = planning_store.get_rows(channel_code, force_refresh=False)
    headers = planning_store.get_fieldnames()
    if not headers:
        return PlanningSpreadsheetResponse(channel=channel_code, headers=[], rows=[])
    result_rows: List[List[Optional[str]]] = []
    for entry in rows:
        raw = dict(entry.raw)
        if entry.script_id:
            raw.setdefault("動画ID", entry.script_id)
        raw.setdefault("チャンネル", entry.channel_code)
        if entry.video_number:
            raw.setdefault("動画番号", entry.video_number)
        row_values = [raw.get(column, "") for column in headers]
        result_rows.append(row_values)
    return PlanningSpreadsheetResponse(channel=channel_code, headers=headers, rows=result_rows)


def _normalize_video_number_token(value: str) -> str:
    token = value.strip()
    if not token:
        raise HTTPException(status_code=400, detail="動画番号を入力してください。")
    if not token.isdigit():
        raise HTTPException(status_code=400, detail="動画番号は数字のみ指定してください。")
    return f"{int(token):03d}"


def _maybe_int_from_token(value: str) -> Optional[int]:
    trimmed = "".join(ch for ch in value if ch.isdigit())
    if not trimmed:
        return None
    try:
        return int(trimmed)
    except ValueError:
        return None


def _read_channel_csv_rows(channel_code: str) -> Tuple[List[str], List[Dict[str, str]]]:
    channel_code = channel_code.upper()
    channel_path = CHANNEL_PLANNING_DIR / f"{channel_code}.csv"
    if channel_path.exists():
        with channel_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
            if fieldnames:
                return fieldnames, rows
    # fallback: use planning_store fieldnames (union) or legacy master columns
    fieldnames = planning_store.get_fieldnames()
    if not fieldnames:
        raise HTTPException(status_code=404, detail="channels CSV は使用しません。channels CSV を利用してください。")
    return list(fieldnames), []


def _write_csv_with_lock(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = [{key: (row.get(key) or "") for key in fieldnames} for row in rows]
    try:
        with portalocker.Lock(
            str(path),
            mode="w",
            encoding="utf-8",
            timeout=LOCK_TIMEOUT_SECONDS,
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(serialised)
            handle.flush()
    except portalocker.exceptions.Timeout as exc:  # pragma: no cover - IO guard
        LOCK_METRICS["timeout"] += 1
        record_lock_event("timeout")
        logger.warning("Lock timeout while writing %s", path)
        raise HTTPException(
            status_code=423,
            detail=f"{path.name} が使用中です。数秒後に再試行してください。",
        ) from exc
    except portalocker.exceptions.LockException as exc:  # pragma: no cover - IO guard
        LOCK_METRICS["unexpected"] += 1
        record_lock_event("unexpected")
        logger.exception("Unexpected lock error for %s", path)
        raise HTTPException(
            status_code=500,
            detail=f"{path.name} の更新中にロックエラーが発生しました。",
        ) from exc


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


def normalize_optional_text(value: Any) -> Optional[str]:
    """Return a stripped string or None when empty/undefined."""

    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    else:
        text = str(value).strip()
    return text or None


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


def _parse_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _iter_planning_rows(channel_filter: Optional[str]):
    _ensure_planning_store_ready()
    if channel_filter:
        targets = [channel_filter]
    else:
        targets = list(planning_store.list_channels())
    for code in targets:
        for entry in planning_store.get_rows(code, force_refresh=False):
            yield entry


def _load_planning_rows(channel_filter: Optional[str]) -> List[PlanningCsvRowResponse]:
    rows: List[PlanningCsvRowResponse] = []
    for entry in _iter_planning_rows(channel_filter):
        raw = dict(entry.raw)
        script_id = entry.script_id or raw.get("動画ID") or raw.get("台本番号") or ""
        script_id = script_id.strip()
        channel_code = entry.channel_code
        raw.setdefault("チャンネル", channel_code)
        if script_id:
            raw.setdefault("動画ID", script_id)
        video_number = entry.video_number or ""
        if not video_number and script_id and "-" in script_id:
            video_number = script_id.split("-", 1)[1]
        if video_number:
            raw.setdefault("動画番号", video_number)
        planning_payload = build_planning_payload_from_row(raw)
        # columns を UI 用にサニタイズ（pydantic ValidationError 回避）
        columns_sanitized: Dict[str, Optional[str]] = {}
        for key, value in raw.items():
            if key is None:
                continue
            k = str(key)
            v: Optional[str]
            if isinstance(value, list):
                v = "\n".join(str(x) for x in value if x is not None)
            else:
                v = str(value) if value not in ("", None) else None
            columns_sanitized[k] = v
        rows.append(
            PlanningCsvRowResponse(
                channel=channel_code,
                video_number=video_number,
                script_id=script_id or None,
                title=normalize_optional_text(raw.get("タイトル")),
                script_path=normalize_optional_text(raw.get("台本")),
                progress=normalize_optional_text(raw.get("進捗")),
                quality_check=normalize_optional_text(raw.get("品質チェック結果")),
                character_count=_parse_int(raw.get("文字数")),
                updated_at=normalize_optional_text(raw.get("更新日時")),
                planning=planning_payload,
                columns=columns_sanitized,
            )
        )
    rows.sort(key=lambda item: (item.channel, item.video_number))
    return rows


def _looks_like_html(headers: List[str], rows: List[List[str]]) -> bool:
    def _sample(values: List[str]) -> str:
        return " ".join(values[:3]).lower()

    if any("<!doctype" in cell.lower() or "<html" in cell.lower() for cell in headers if cell):
        return True
    for row in rows[:2]:
        if any("<!doctype" in cell.lower() or "<html" in cell.lower() for cell in row if cell):
            return True
    combined = _sample(headers)
    if combined and ("login" in combined and "google" in combined):
        return True
    return False


def _load_channel_spreadsheet(channel_code: str) -> PlanningSpreadsheetResponse:
    csv_path = SPREADSHEET_EXPORT_DIR / f"{channel_code}.csv"
    if csv_path.exists():
        headers, rows = _read_csv_file(csv_path)
        if headers and not _looks_like_html(headers, rows):
            return PlanningSpreadsheetResponse(channel=channel_code, headers=headers, rows=rows)
        logger.warning("%s は有効な CSV として解析できません。planning SoT から再構成します。", csv_path)
    return _build_spreadsheet_from_planning(channel_code)


def normalize_planning_video_number(value: Any) -> Optional[str]:
    """
    Best-effort normalization for Planning SoT columns ("動画番号" etc).

    - Extract digits (so inputs like "1", "001", "第1話" do not crash the API).
    - Return `None` when no numeric token is found.

    This is intentionally more permissive than `normalize_video_number`, which validates
    user-provided path params strictly.
    """

    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    return digits.zfill(3)


def video_base_dir(channel_code: str, video_number: str) -> Path:
    return DATA_ROOT / channel_code / video_number


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


def safe_relative_path(path: Path) -> Optional[str]:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path) if path.exists() else None


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


def get_audio_duration_seconds(path: Path) -> Optional[float]:
    if not path.exists():
        return None
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            if rate:
                return round(frames / float(rate), 3)
    except (wave.Error, OSError):  # wave.Error for invalid WAV, OSError for unreadable file
        return None
    return None


def load_status(channel_code: str, video_number: str) -> dict:
    status_path = DATA_ROOT / channel_code / video_number / "status.json"
    if not status_path.exists():
        raise HTTPException(status_code=404, detail="status.json not found")
    return load_json(status_path)


def load_status_optional(channel_code: str, video_number: str) -> Optional[dict]:
    status_path = DATA_ROOT / channel_code / video_number / "status.json"
    if not status_path.exists():
        return None
    return load_json(status_path)


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


def _detect_artifact_path(channel_code: str, video_number: str, extension: str) -> Path:
    base = audio_final_dir(channel_code, video_number)
    if extension == ".wav":
        for ext in (".wav", ".flac", ".mp3", ".m4a"):
            candidate = base / f"{channel_code}-{video_number}{ext}"
            if candidate.exists():
                return candidate
    return base / f"{channel_code}-{video_number}{extension}"


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


def resolve_text_file(path: Path) -> Optional[str]:
    """正規パスのみを読む。フォールバック禁止。"""
    if not path.exists() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def resolve_audio_path(status: dict, base_dir: Path) -> Optional[Path]:
    channel = normalize_channel_code(status.get("channel") or base_dir.parent.name)
    video_no = normalize_video_number(str(status.get("video_number") or base_dir.name))
    metadata = status.get("metadata", {}) if isinstance(status, dict) else {}
    audio_meta = metadata.get("audio", {}) if isinstance(metadata, dict) else {}
    synth_meta = audio_meta.get("synthesis", {}) if isinstance(audio_meta, dict) else {}
    final_wav = synth_meta.get("final_wav") if isinstance(synth_meta, dict) else None
    if final_wav:
        candidate = Path(str(final_wav))
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve()
        if candidate.exists():
            return candidate.resolve()

    final_candidate = _detect_artifact_path(channel, video_no, ".wav")
    if final_candidate.exists():
        return final_candidate.resolve()

    legacy_candidate = base_dir / "audio_prep" / f"{channel}-{video_no}.wav"
    return legacy_candidate.resolve() if legacy_candidate.exists() else None


def resolve_log_path(status: dict, base_dir: Path) -> Optional[Path]:
    channel = normalize_channel_code(status.get("channel") or base_dir.parent.name)
    video_no = normalize_video_number(str(status.get("video_number") or base_dir.name))
    final_log = audio_final_dir(channel, video_no) / "log.json"
    if final_log.exists():
        return final_log.resolve()
    candidate = base_dir / "audio_prep" / "log.json"
    if candidate.exists():
        return candidate.resolve()
    candidate_nested = base_dir / "audio_prep" / f"{channel}-{video_no}.log.json"
    return candidate_nested.resolve() if candidate_nested.exists() else None


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


def resolve_srt_path(status: dict, base_dir: Path) -> Optional[Path]:
    channel = normalize_channel_code(status.get("channel") or base_dir.parent.name)
    video_no = normalize_video_number(str(status.get("video_number") or base_dir.name))
    metadata = status.get("metadata", {}) if isinstance(status, dict) else {}
    srt_meta = metadata.get("subtitles", {}) if isinstance(metadata, dict) else {}
    final_srt = srt_meta.get("final_srt") if isinstance(srt_meta, dict) else None
    if final_srt:
        candidate = Path(str(final_srt))
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve()
        if candidate.exists():
            return candidate.resolve()

    final_candidate = _detect_artifact_path(channel, video_no, ".srt")
    if final_candidate.exists():
        return final_candidate.resolve()

    legacy_candidate = base_dir / "audio_prep" / f"{channel}-{video_no}.srt"
    return legacy_candidate.resolve() if legacy_candidate.exists() else None

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


class PlanningFieldsPayload(BaseModel):
    thumbnail_upper: Optional[str] = None
    thumbnail_lower: Optional[str] = None
    thumbnail_prompt: Optional[str] = None
    concept_intent: Optional[str] = None
    target_audience: Optional[str] = None
    outline_notes: Optional[str] = None
    dalle_prompt: Optional[str] = None
    script_sample: Optional[str] = None
    thumbnail_title: Optional[str] = None


class PlanningUpdateRequest(OptimisticUpdateRequest):
    fields: PlanningFieldsPayload


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


class NaturalCommandAction(BaseModel):
    type: Literal["replace", "insert_pause"]
    target: Literal["tts", "assembled", "srt"] = "tts"
    original: Optional[str] = None
    replacement: Optional[str] = None
    scope: Literal["first", "all"] = "first"
    update_assembled: bool = True
    regenerate_audio: bool = False
    pause_seconds: Optional[float] = None
    pause_scope: Literal["cursor", "line_end", "section_end"] = "cursor"

    @model_validator(mode="after")
    def _validate_payload(self) -> "NaturalCommandAction":
        if self.type == "replace":
            if not self.original or not self.replacement:
                raise ValueError("Replace action must include original and replacement text.")
        elif self.type == "insert_pause":
            if self.pause_seconds is None:
                raise ValueError("Insert pause action must include pause_seconds.")
            if self.pause_seconds <= 0:
                raise ValueError("pause_seconds must be greater than zero.")
        return self


class NaturalCommandRequest(OptimisticUpdateRequest):
    command: str


class NaturalCommandResponse(BaseModel):
    actions: List[NaturalCommandAction]
    message: Optional[str] = None


class PlanningFieldPayload(BaseModel):
    key: str
    column: str
    label: str
    value: Optional[str] = None


class PlanningInfoResponse(BaseModel):
    creation_flag: Optional[str] = None
    fields: List[PlanningFieldPayload] = Field(default_factory=list)


class PlanningUpdateRequest(OptimisticUpdateRequest):
    creation_flag: Optional[str] = Field(
        None,
        description="G列（作成フラグ）の値。空文字またはnullでリセット。",
    )
    fields: Dict[str, Optional[str]] = Field(
        default_factory=dict,
        description="任意フィールドの更新（キー: optional_fields_registry の内部キー）",
    )

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
        invalid = [key for key in value if key not in FIELD_KEYS]
        if invalid:
            joined = ", ".join(sorted(invalid))
            raise HTTPException(status_code=400, detail=f"不明な企画フィールドが指定されました: {joined}")
        return value


class PlanningUpdateResponse(BaseModel):
    status: str
    updated_at: str
    planning: PlanningInfoResponse


class PlanningCreateRequest(BaseModel):
    channel: str = Field(..., description="CHコード（例: CH01）")
    video_number: str = Field(..., description="動画番号（数字）")
    title: str = Field(..., description="企画タイトル")
    no: Optional[str] = Field(None, description="No. 列。省略時は動画番号を使用。")
    creation_flag: Optional[str] = Field("3", description="G列（作成フラグ）の初期値")
    progress: Optional[str] = Field("topic_research: pending", description="進捗列の初期値")
    fields: Dict[str, Optional[str]] = Field(
        default_factory=dict,
        description="optional_fields_registry のキーに対応するフィールド値",
    )

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, value: str) -> str:
        token = value.strip().upper()
        if not token.startswith("CH"):
            raise HTTPException(status_code=400, detail="channel は CH で始まるコードを指定してください。")
        return token

    @field_validator("video_number")
    @classmethod
    def validate_video_number(cls, value: str) -> str:
        _normalize_video_number_token(value)
        return value

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="タイトルを入力してください。")
        return normalized

    @field_validator("fields")
    @classmethod
    def validate_create_fields(cls, value: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
        invalid = [key for key in value if key not in FIELD_KEYS]
        if invalid:
            joined = ", ".join(sorted(invalid))
            raise HTTPException(status_code=400, detail=f"不明な企画フィールドが指定されました: {joined}")
        return value


class PlanningCsvRowResponse(BaseModel):
    channel: str
    video_number: str
    script_id: Optional[str] = None
    title: Optional[str] = None
    script_path: Optional[str] = None
    progress: Optional[str] = None
    quality_check: Optional[str] = None
    character_count: Optional[int] = None
    updated_at: Optional[str] = None
    planning: Optional[PlanningInfoResponse] = None
    columns: Dict[str, Optional[str]] = Field(default_factory=dict)


class PlanningProgressUpdateRequest(BaseModel):
    progress: str = Field(..., description="企画CSVの進捗列を更新する。")
    expected_updated_at: Optional[str] = Field(
        default=None,
        description="競合検知用の更新トークン（CSVの更新日時列）。列が未存在/空の場合はベストエフォートで更新する。",
    )


class PlanningSpreadsheetResponse(BaseModel):
    channel: str
    headers: List[str]
    rows: List[List[Optional[str]]]


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


class RedoUpdateRequest(BaseModel):
    redo_script: Optional[bool] = None
    redo_audio: Optional[bool] = None
    redo_note: Optional[str] = None


class RedoUpdateResponse(BaseModel):
    status: str
    redo_script: bool
    redo_audio: bool
    redo_note: Optional[str] = None
    updated_at: str


class ThumbnailOverrideRequest(BaseModel):
    thumbnail_url: str
    thumbnail_path: Optional[str] = None


class ThumbnailOverrideResponse(BaseModel):
    status: str
    thumbnail_url: str
    thumbnail_path: Optional[str] = None
    updated_at: str


def build_planning_payload(metadata: Dict[str, Any]) -> PlanningInfoResponse:
    """Convert metadata.planning and sheet_flag into API payload."""

    planning_section = get_planning_section(metadata)
    fields: List[PlanningFieldPayload] = []
    for column_name, key in OPTIONAL_FIELDS.items():
        fields.append(
            PlanningFieldPayload(
                key=key,
                column=column_name,
                label=column_name,
                value=normalize_optional_text(planning_section.get(key)),
            )
        )
    flag_value = normalize_optional_text(metadata.get("sheet_flag"))
    return PlanningInfoResponse(creation_flag=flag_value, fields=fields)


def build_planning_payload_from_row(row: Dict[str, str]) -> PlanningInfoResponse:
    fields: List[PlanningFieldPayload] = []
    for column_name, key in OPTIONAL_FIELDS.items():
        fields.append(
            PlanningFieldPayload(
                key=key,
                column=column_name,
                label=column_name,
                value=normalize_optional_text(row.get(column_name)),
            )
        )
    return PlanningInfoResponse(
        creation_flag=normalize_optional_text(row.get("作成フラグ")),
        fields=fields,
    )


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


class SRTIssue(BaseModel):
    type: str
    detail: str
    block: Optional[int] = None
    start: Optional[float] = None
    end: Optional[float] = None


class SRTVerifyResponse(BaseModel):
    valid: bool
    audio_duration_seconds: Optional[float]
    srt_duration_seconds: Optional[float]
    diff_ms: Optional[float]
    issues: List[SRTIssue]


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


class ThumbnailQcNoteUpdateRequest(BaseModel):
    relative_path: str = Field(..., min_length=1)
    note: Optional[str] = None


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


IMAGE_MODEL_ROUTING_SCHEMA_V1 = "ytm.settings.image_model_routing.v1"


class ImageModelKeyInfo(BaseModel):
    key: str
    provider: str
    model_name: str


class ImageModelCatalogOption(BaseModel):
    id: str
    label: str
    provider_group: str
    variant: str
    model_key: Optional[str] = None
    enabled: bool = True
    note: Optional[str] = None


class ImageModelRoutingCatalog(BaseModel):
    thumbnail: List[ImageModelCatalogOption] = Field(default_factory=list)
    video_image: List[ImageModelCatalogOption] = Field(default_factory=list)


class ImageModelRoutingSelection(BaseModel):
    model_key: Optional[str] = None
    provider: Optional[str] = None
    model_name: Optional[str] = None
    source: str
    missing: bool = False
    blocked: bool = False
    note: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class ChannelImageModelRouting(BaseModel):
    channel: str
    thumbnail: ImageModelRoutingSelection
    video_image: ImageModelRoutingSelection


class ImageModelRoutingResponse(BaseModel):
    schema: str = IMAGE_MODEL_ROUTING_SCHEMA_V1
    generated_at: str
    blocked_model_keys: List[str] = Field(default_factory=list)
    models: List[ImageModelKeyInfo] = Field(default_factory=list)
    catalog: ImageModelRoutingCatalog = Field(default_factory=ImageModelRoutingCatalog)
    channels: List[ChannelImageModelRouting] = Field(default_factory=list)


class ImageModelRoutingUpdate(BaseModel):
    thumbnail_model_key: Optional[str] = None
    video_image_model_key: Optional[str] = None


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
    from backend.routers import video_state
    app.include_router(video_state.router)
except Exception as e:
    logger.error("Failed to load video_state router: %s", e)

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
    from backend.routers import thumbnails_overrides

    app.include_router(thumbnails_overrides.router)
except Exception as e:
    logger.error("Failed to load thumbnails_overrides router: %s", e)

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


@app.post("/api/channels/{channel}/videos", status_code=201)
def register_video(channel: str, payload: VideoCreateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(payload.video)
    base_dir = video_base_dir(channel_code, video_number)
    status_path = base_dir / "status.json"
    if status_path.exists():
        raise HTTPException(status_code=409, detail="既に status.json が存在します。")

    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "content").mkdir(parents=True, exist_ok=True)
    (base_dir / "audio_prep").mkdir(parents=True, exist_ok=True)

    files_dict: Dict[str, str] = {}
    if payload.files:
        files_dict = payload.files.model_dump(exclude_none=True)

    # デフォルトのファイルを用意
    assembled_path = files_dict.get("assembled")
    if not assembled_path:
        default_assembled = base_dir / "content" / "assembled.md"
        if not default_assembled.exists():
            default_assembled.write_text("", encoding="utf-8")
        files_dict["assembled"] = str(default_assembled.relative_to(PROJECT_ROOT))
    else:
        assembled_file = Path(assembled_path)
        if not assembled_file.is_absolute():
            assembled_file = (PROJECT_ROOT / assembled_path).resolve()
        assembled_file.parent.mkdir(parents=True, exist_ok=True)
        if not assembled_file.exists():
            assembled_file.write_text("", encoding="utf-8")
        files_dict["assembled"] = str(safe_relative_path(assembled_file) or assembled_file)

    tts_path = files_dict.get("tts")
    if not tts_path:
        default_tts = base_dir / "audio_prep" / "script_sanitized.txt"
        if not default_tts.exists():
            default_tts.write_text("", encoding="utf-8")
        files_dict["tts"] = str(default_tts.relative_to(PROJECT_ROOT))
    else:
        tts_file = Path(tts_path)
        if not tts_file.is_absolute():
            tts_file = (PROJECT_ROOT / tts_path).resolve()
        tts_file.parent.mkdir(parents=True, exist_ok=True)
        if not tts_file.exists():
            tts_file.write_text("", encoding="utf-8")
        files_dict["tts"] = str(safe_relative_path(tts_file) or tts_file)

    file_refs = VideoFileReferences.model_validate(files_dict)

    status_payload = build_status_payload(
        channel_code=channel_code,
        video_number=video_number,
        script_id=payload.script_id,
        title=payload.title,
        initial_stage=payload.initial_stage,
        status_value=payload.status,
        metadata_patch=payload.metadata,
        generation=payload.generation,
        files=file_refs,
    )

    save_status(channel_code, video_number, status_payload)

    return {
        "status": "ok",
        "channel": channel_code,
        "video": video_number,
        "updated_at": status_payload["updated_at"],
    }


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


@app.get("/api/planning", response_model=List[PlanningCsvRowResponse])
def list_planning_rows(channel: Optional[str] = Query(None, description="CHコード (例: CH06)")):
    channel_code = normalize_channel_code(channel) if channel else None
    return _load_planning_rows(channel_code)

@app.get("/api/planning/spreadsheet", response_model=PlanningSpreadsheetResponse)
def get_planning_spreadsheet(channel: str = Query(..., description="CHコード (例: CH06)")):
    channel_code = normalize_channel_code(channel)
    return _load_channel_spreadsheet(channel_code)


@app.post("/api/planning", response_model=PlanningCsvRowResponse, status_code=201)
def create_planning_entry(payload: PlanningCreateRequest):
    channel_code = normalize_channel_code(payload.channel)
    video_token = _normalize_video_number_token(payload.video_number)
    numeric_video = _maybe_int_from_token(video_token)
    fieldnames, rows = _read_channel_csv_rows(channel_code)
    fields_payload: Dict[str, Optional[str]] = dict(payload.fields)

    def _row_matches(entry: Dict[str, str]) -> bool:
        if (entry.get("チャンネル") or "").strip().upper() != channel_code:
            return False
        raw_value = entry.get("動画番号") or entry.get("No.") or ""
        if not raw_value:
            return False
        try:
            existing_token = _normalize_video_number_token(raw_value)
        except HTTPException:
            existing_token = raw_value.strip()
        return existing_token == video_token

    if any(_row_matches(row) for row in rows):
        raise HTTPException(status_code=409, detail=f"{channel_code}-{video_token} は既に存在します。")

    persona_text = planning_requirements.get_channel_persona(channel_code)
    target_override = normalize_optional_text(fields_payload.pop("target_audience", None))
    if persona_text:
        if target_override and target_override != persona_text:
            raise HTTPException(
                status_code=400,
                detail="ターゲット層はSSOTの共通ペルソナに固定されています。",
            )
    elif target_override:
        persona_text = target_override

    description_defaults = planning_requirements.get_description_defaults(channel_code)
    for key, default_value in description_defaults.items():
        if not normalize_optional_text(fields_payload.get(key)):
            fields_payload[key] = default_value

    required_keys = planning_requirements.resolve_required_field_keys(channel_code, numeric_video)
    missing_keys = [key for key in required_keys if not normalize_optional_text(fields_payload.get(key))]
    if missing_keys:
        missing_columns = [FIELD_KEYS.get(key, key) for key in missing_keys]
        raise HTTPException(
            status_code=400,
            detail=f"必須フィールドが未入力です: {', '.join(missing_columns)}",
        )

    # Add optional/required columns that are about to be written
    dynamic_columns = []
    for field_key in fields_payload.keys():
        column = FIELD_KEYS.get(field_key)
        if column:
            dynamic_columns.append(column)
    if persona_text:
        dynamic_columns.append("ターゲット層")
    for col in dynamic_columns:
        if col not in fieldnames:
            fieldnames.append(col)

    script_id = f"{channel_code}-{video_token}"
    new_row = {column: "" for column in fieldnames}
    if "チャンネル" in new_row:
        new_row["チャンネル"] = channel_code
    if "No." in new_row:
        if payload.no:
            new_row["No."] = payload.no.strip()
        else:
            new_row["No."] = str(int(video_token))
    if "動画番号" in new_row:
        new_row["動画番号"] = video_token
    if "動画ID" in new_row:
        new_row["動画ID"] = script_id
    if "台本番号" in new_row:
        new_row["台本番号"] = script_id
    new_row["タイトル"] = payload.title.strip()
    new_row["台本"] = new_row.get("台本", "")
    new_row["作成フラグ"] = payload.creation_flag or ""
    new_row["進捗"] = payload.progress or "topic_research: pending"
    new_row["品質チェック結果"] = new_row.get("品質チェック結果") or "未完了"
    new_row["文字数"] = new_row.get("文字数", "")
    new_row["納品"] = new_row.get("納品", "")
    new_row["更新日時"] = current_timestamp()

    for field_key, value in fields_payload.items():
        column = FIELD_KEYS.get(field_key)
        if column:
            text_value = normalize_optional_text(value) or ""
            new_row[column] = text_value

    if persona_text and "ターゲット層" in new_row:
        new_row["ターゲット層"] = persona_text

    rows.append(new_row)
    CHANNEL_PLANNING_DIR.mkdir(parents=True, exist_ok=True)
    channel_path = CHANNEL_PLANNING_DIR / f"{channel_code}.csv"
    _write_csv_with_lock(channel_path, fieldnames, rows)

    planning_payload = build_planning_payload_from_row(new_row)
    character_count_raw = new_row.get("文字数")
    try:
        character_value = int(character_count_raw) if character_count_raw else None
    except ValueError:
        character_value = None

    return PlanningCsvRowResponse(
        channel=channel_code,
        video_number=video_token,
        script_id=script_id,
        title=new_row.get("タイトル"),
        script_path=new_row.get("台本"),
        progress=new_row.get("進捗"),
        quality_check=new_row.get("品質チェック結果"),
        character_count=character_value,
        updated_at=new_row.get("更新日時"),
        planning=planning_payload,
        columns=new_row,
    )


@app.put(
    "/api/planning/channels/{channel_code}/{video_number}/progress",
    response_model=PlanningCsvRowResponse,
)
def update_planning_channel_progress(channel_code: str, video_number: str, payload: PlanningProgressUpdateRequest):
    channel_code = normalize_channel_code(channel_code)
    video_token = _normalize_video_number_token(video_number)
    csv_path = CHANNEL_PLANNING_DIR / f"{channel_code}.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="planning csv not found")

    fieldnames, rows = _read_channel_csv_rows(channel_code)
    if "進捗" not in fieldnames:
        fieldnames.append("進捗")
    if "更新日時" not in fieldnames:
        fieldnames.append("更新日時")

    target_row: Optional[Dict[str, str]] = None
    for row in rows:
        row_channel = (row.get("チャンネル") or "").strip().upper()
        if row_channel and row_channel != channel_code:
            continue
        raw_video = row.get("動画番号") or row.get("No.") or ""
        if not raw_video:
            continue
        try:
            existing_token = _normalize_video_number_token(str(raw_video))
        except HTTPException:
            continue
        if existing_token == video_token:
            target_row = row
            break

    if target_row is None:
        raise HTTPException(status_code=404, detail=f"{channel_code}-{video_token} の企画行が見つかりません。")

    current_updated_at = normalize_optional_text(target_row.get("更新日時"))
    expected_updated_at = normalize_optional_text(payload.expected_updated_at)
    if expected_updated_at is not None and current_updated_at:
        if expected_updated_at != current_updated_at:
            raise HTTPException(
                status_code=409,
                detail="他のセッションで更新されました。最新の情報を再取得してからもう一度保存してください。",
            )

    normalized_progress = payload.progress.strip()
    current_progress = str(target_row.get("進捗") or "").strip()
    if normalized_progress != current_progress:
        target_row["進捗"] = normalized_progress
        target_row["更新日時"] = current_timestamp()
        _write_csv_with_lock(csv_path, fieldnames, rows)

    script_id = (
        normalize_optional_text(target_row.get("台本番号"))
        or normalize_optional_text(target_row.get("動画ID"))
        or f"{channel_code}-{video_token}"
    )
    planning_payload = build_planning_payload_from_row(target_row)
    character_count_raw = target_row.get("文字数")
    try:
        character_value = int(character_count_raw) if character_count_raw else None
    except ValueError:
        character_value = None

    return PlanningCsvRowResponse(
        channel=channel_code,
        video_number=video_token,
        script_id=script_id,
        title=normalize_optional_text(target_row.get("タイトル")),
        script_path=normalize_optional_text(target_row.get("台本")),
        progress=normalize_optional_text(target_row.get("進捗")),
        quality_check=normalize_optional_text(target_row.get("品質チェック結果")),
        character_count=character_value,
        updated_at=normalize_optional_text(target_row.get("更新日時")),
        planning=planning_payload,
        columns=target_row,
    )


@app.put("/api/channels/{channel}/profile", response_model=ChannelProfileResponse)
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


@app.post("/api/channels/{channel}/videos/{video}/command", response_model=NaturalCommandResponse)
def run_natural_command(channel: str, video: str, payload: NaturalCommandRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)

    detail = get_video_detail(channel, video)
    tts_content = detail.tts_content or ""

    actions, message = interpret_natural_command(payload.command, tts_content)
    for action in actions:
        if action.type == "insert_pause":
            pause_value = action.pause_seconds or 0.0
            append_audio_history_entry(
                channel_code,
                video_number,
                {
                    "event": "tts_command",
                    "status": "suggested",
                    "message": f"LLM suggested pause tag ({pause_value:.2f}s)",
                },
            )
        elif action.type == "replace" and action.original and action.replacement:
            append_audio_history_entry(
                channel_code,
                video_number,
                {
                    "event": "tts_command",
                    "status": "suggested",
                    "message": f"LLM suggested replace '{action.original}' → '{action.replacement}'",
                },
            )
    return NaturalCommandResponse(actions=actions, message=message)


VIDEO_RUN_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _utc_iso_from_mtime(mtime: Optional[float]) -> Optional[str]:
    if mtime is None:
        return None
    try:
        return datetime.fromtimestamp(float(mtime), timezone.utc).isoformat()
    except Exception:
        return None


def _safe_mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except Exception:
        return None


def _min_iso_timestamp(values: Iterable[Optional[str]]) -> Optional[str]:
    best_value: Optional[str] = None
    best_dt: Optional[datetime] = None
    for value in values:
        dt = parse_iso_datetime(value)
        if not dt:
            continue
        if best_dt is None or dt < best_dt:
            best_dt = dt
            best_value = value
    return best_value


def _video_run_recency_key(run_dir: Path) -> float:
    candidates = (
        run_dir,
        run_dir / "auto_run_info.json",
        run_dir / "image_cues.json",
        run_dir / "visual_cues_plan.json",
        run_dir / "images",
    )
    mtimes: List[float] = []
    for path in candidates:
        mtime = _safe_mtime(path)
        if mtime is not None:
            mtimes.append(float(mtime))
    return max(mtimes) if mtimes else 0.0


def _pick_latest_video_run_dirs(channel_code: str, video_numbers: set[str]) -> Dict[str, Path]:
    root = ssot_video_runs_root()
    if not root.exists() or not root.is_dir():
        return {}

    pattern = re.compile(rf"^{re.escape(channel_code)}-(\d{{3}})", re.IGNORECASE)
    best: Dict[str, Tuple[float, Path]] = {}
    try:
        run_dirs = list(root.iterdir())
    except Exception:
        return {}

    for run_dir in run_dirs:
        if not run_dir.is_dir():
            continue
        match = pattern.match(run_dir.name)
        if not match:
            continue
        video_number = match.group(1).zfill(3)
        if video_number not in video_numbers:
            continue
        score = _video_run_recency_key(run_dir)
        previous = best.get(video_number)
        if previous is None or score > previous[0]:
            best[video_number] = (score, run_dir)

    return {video_number: run_dir for video_number, (_, run_dir) in best.items()}


def _compute_video_images_progress(run_dir: Path) -> VideoImagesProgressResponse:
    run_id = run_dir.name

    cue_count: Optional[int] = None
    prompt_count: Optional[int] = None
    prompt_ready = False
    prompt_ready_at: Optional[str] = None

    cues_path = run_dir / "image_cues.json"
    cues_mtime = _safe_mtime(cues_path)
    if cues_mtime is not None:
        prompt_ready_at = _utc_iso_from_mtime(cues_mtime)
    if cues_path.exists() and cues_path.is_file():
        try:
            raw = json.loads(cues_path.read_text(encoding="utf-8"))
        except Exception:
            raw = None
        cues = raw.get("cues") if isinstance(raw, dict) else None
        if isinstance(cues, list):
            cue_count = len(cues)
            prompt_count = 0
            for cue in cues:
                if not isinstance(cue, dict):
                    continue
                prompt_value = (
                    str(cue.get("refined_prompt") or cue.get("prompt") or cue.get("summary") or "").strip()
                )
                if prompt_value:
                    prompt_count += 1
            prompt_ready = bool(prompt_count)

    images_count = 0
    latest_image_mtime: Optional[float] = None
    images_dir = run_dir / "images"
    if images_dir.exists() and images_dir.is_dir():
        try:
            for child in images_dir.iterdir():
                if not child.is_file():
                    continue
                if child.suffix.lower() not in VIDEO_RUN_IMAGE_EXTENSIONS:
                    continue
                images_count += 1
                mtime = _safe_mtime(child)
                if mtime is None:
                    continue
                latest_image_mtime = mtime if latest_image_mtime is None else max(latest_image_mtime, mtime)
        except Exception:
            images_count = 0
            latest_image_mtime = None

    images_complete = False
    if cue_count is not None and cue_count > 0:
        images_complete = images_count >= cue_count

    return VideoImagesProgressResponse(
        run_id=run_id,
        prompt_ready=bool(prompt_ready),
        prompt_ready_at=prompt_ready_at,
        cue_count=cue_count,
        prompt_count=prompt_count,
        images_count=int(images_count),
        images_complete=bool(images_complete),
        images_updated_at=_utc_iso_from_mtime(latest_image_mtime),
    )


def _build_video_images_progress_map(channel_code: str, video_numbers: set[str]) -> Dict[str, VideoImagesProgressResponse]:
    run_dirs = _pick_latest_video_run_dirs(channel_code, video_numbers)
    out: Dict[str, VideoImagesProgressResponse] = {}
    for video_number, run_dir in run_dirs.items():
        out[video_number] = _compute_video_images_progress(run_dir)
    return out


def _build_thumbnail_progress_map(channel_code: str, video_numbers: set[str]) -> Dict[str, ThumbnailProgressResponse]:
    project_map: Dict[str, dict] = {}
    with THUMBNAIL_PROJECTS_LOCK:
        _, document = _load_thumbnail_projects_document()
    projects = document.get("projects") if isinstance(document, dict) else None
    if isinstance(projects, list):
        for project in projects:
            if not isinstance(project, dict):
                continue
            proj_channel = str(project.get("channel") or "").strip().upper()
            if proj_channel != channel_code:
                continue
            video_number = normalize_planning_video_number(project.get("video"))
            if not video_number or video_number not in video_numbers:
                continue
            project_map[video_number] = project

    # Disk fallback: allow detecting "created" even if projects.json is stale.
    disk_variants_map = _collect_disk_thumbnail_variants(channel_code)

    out: Dict[str, ThumbnailProgressResponse] = {}
    for video_number in video_numbers:
        project = project_map.get(video_number)
        status = str(project.get("status") or "").strip().lower() if isinstance(project, dict) else ""
        if status not in THUMBNAIL_PROJECT_STATUSES:
            status = ""
        status_updated_at = project.get("status_updated_at") if isinstance(project, dict) else None
        qc_cleared = status in {"approved", "published"}
        qc_cleared_at = status_updated_at if qc_cleared else None

        variant_created_at: List[Optional[str]] = []
        variant_count = 0
        variants = project.get("variants") if isinstance(project, dict) else None
        if isinstance(variants, list):
            variant_count = len(variants)
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                variant_created_at.append(variant.get("created_at"))

        disk_variants = disk_variants_map.get(video_number) or []
        if disk_variants and variant_count <= 0:
            variant_count = len(disk_variants)
        for variant in disk_variants:
            if not isinstance(variant, ThumbnailVariantResponse):
                continue
            variant_created_at.append(variant.created_at)

        created_at = _min_iso_timestamp(variant_created_at)
        created = bool(created_at)

        out[video_number] = ThumbnailProgressResponse(
            created=bool(created),
            created_at=created_at,
            qc_cleared=bool(qc_cleared),
            qc_cleared_at=qc_cleared_at,
            status=status or None,
            variant_count=int(variant_count),
        )
    return out


@app.get("/api/channels/{channel}/videos", response_model=List[VideoSummaryResponse])
def list_videos(channel: str):
    channel_code = normalize_channel_code(channel)
    planning_rows: Dict[str, planning_store.PlanningRow] = {}
    for row in planning_store.get_rows(channel_code, force_refresh=True):
        token = normalize_planning_video_number(row.video_number)
        if not token:
            continue
        planning_rows[token] = row
    video_dirs = list_video_dirs(channel_code)
    video_numbers = set(p.name for p in video_dirs)
    video_numbers.update(planning_rows.keys())
    thumbnail_progress_map = _build_thumbnail_progress_map(channel_code, video_numbers)
    video_images_progress_map = _build_video_images_progress_map(channel_code, video_numbers)
    response: List[VideoSummaryResponse] = []
    for video_number in sorted(video_numbers):
        planning_row = planning_rows.get(video_number)
        a_text_character_count_raw: Optional[int] = _character_count_from_a_text(channel_code, video_number)
        a_text_character_count = a_text_character_count_raw if a_text_character_count_raw is not None else 0
        planning_character_count: Optional[int] = None
        character_count = a_text_character_count
        try:
            status = load_status(channel_code, video_number)
        except HTTPException as exc:
            if exc.status_code == 404:
                status = None
            else:
                raise
        metadata = status.get("metadata", {}) if status else {}
        if not isinstance(metadata, dict):
            metadata = {}
        stages_raw = status.get("stages", {}) if status else {}
        stages_dict, a_text_ok, audio_exists, srt_exists = _derive_effective_stages(
            channel_code=channel_code,
            video_number=video_number,
            stages=stages_raw if isinstance(stages_raw, dict) else {},
            metadata=metadata,
        )
        stages = (
            {key: _stage_status_value(value) for key, value in stages_dict.items() if key}
            if isinstance(stages_dict, dict)
            else {}
        )
        raw_status_value = status.get("status", "unknown") if status else "pending"
        status_value = raw_status_value
        if planning_row:
            row_raw = planning_row.raw
            # CSV を最新ソースとして統合する
            if row_raw.get("タイトル"):
                metadata["sheet_title"] = row_raw.get("タイトル")
            if row_raw.get("作成フラグ"):
                metadata["sheet_flag"] = row_raw.get("作成フラグ")
            planning_section = get_planning_section(metadata)
            update_planning_from_row(planning_section, row_raw)
            raw_chars = row_raw.get("文字数")
            if isinstance(raw_chars, (int, float)):
                planning_character_count = int(raw_chars)
            elif isinstance(raw_chars, str) and raw_chars.strip():
                try:
                    planning_character_count = int(raw_chars.replace(",", ""))
                except ValueError:
                    planning_character_count = None

        # 投稿済み（ロック）:
        # - 正本は Planning CSV の「進捗」（人間が手動で更新するのは基本ここだけ）。
        # - status.json の metadata.published_lock は補助ソース。
        progress_value = ""
        if planning_row:
            progress_value = str(planning_row.raw.get("進捗") or planning_row.raw.get("progress") or "").strip()

        published_locked = False
        if progress_value:
            lower = progress_value.lower()
            if "投稿済み" in progress_value or "公開済み" in progress_value or lower in {"published", "posted"}:
                published_locked = True
        if not published_locked and isinstance(metadata, dict) and bool(metadata.get("published_lock")):
            published_locked = True
        if published_locked:
            stages["audio_synthesis"] = "completed"
            stages["srt_generation"] = "completed"

        status_value = _derive_effective_video_status(
            raw_status=raw_status_value,
            stages=stages_dict,
            a_text_ok=a_text_ok,
            audio_exists=audio_exists,
            srt_exists=srt_exists,
            published_locked=published_locked,
        )
        script_validated = _stage_status_value(stages_dict.get("script_validation")) == "completed" or str(
            raw_status_value or ""
        ).strip().lower() == "script_validated"
        ready_for_audio = bool(metadata.get("ready_for_audio", False)) or script_validated
        a_text_exists = bool(a_text_ok)
        if published_locked:
            status_value = "completed"

        updated_at = status.get("updated_at") if status else None
        if not updated_at and planning_row:
            fallback_updated_at = str(planning_row.raw.get("更新日時") or "").strip()
            if fallback_updated_at:
                updated_at = fallback_updated_at
        youtube_description = _build_youtube_description(
            channel_code, video_number, metadata, metadata.get("title") or metadata.get("sheet_title")
        )
        planning_payload = (
            build_planning_payload(metadata)
            if metadata
            else build_planning_payload_from_row(planning_row.raw) if planning_row else PlanningInfoResponse(creation_flag=None, fields=[])
        )
        response.append(
            VideoSummaryResponse(
                video=video_number,
                script_id=status.get("script_id") if status else planning_row.script_id if planning_row else None,
                title=metadata.get("sheet_title") or metadata.get("title") or (planning_row.raw.get("タイトル") if planning_row else "(draft)"),
                status=status_value,
                ready_for_audio=bool(ready_for_audio),
                published_lock=bool(published_locked),
                stages=stages,
                updated_at=updated_at,
                character_count=character_count,
                a_text_exists=a_text_exists,
                a_text_character_count=a_text_character_count,
                planning_character_count=planning_character_count,
                planning=planning_payload,
                youtube_description=youtube_description,
                thumbnail_progress=thumbnail_progress_map.get(video_number),
                video_images_progress=video_images_progress_map.get(video_number),
            )
        )
    return response


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


def _count_manual_pauses(history: Any) -> int:
    if not isinstance(history, list):
        return 0
    count = 0
    for entry in history:
        if not isinstance(entry, dict):
            continue
        event = str(entry.get("event", "")).lower()
        message = str(entry.get("message", "") or "").lower()
        if "manual" in event or "manual" in message:
            count += 1
    return count


@app.get("/api/workspaces/audio-review", response_model=List[AudioReviewItemResponse])
def list_audio_review_items(
    channel: Optional[str] = Query(default=None, description="フィルタ対象のチャンネルコード"),
    status: Optional[str] = Query(default=None, description="フィルタ対象の案件ステータス"),
    video: Optional[str] = Query(default=None, description="フィルタ対象の動画番号（3桁）"),
):
    channel_filter: Optional[str]
    if isinstance(channel, str) and channel.strip() and channel.strip().lower() != "all":
        channel_filter = normalize_channel_code(channel)
    else:
        channel_filter = None

    video_filter: Optional[str]
    if isinstance(video, str) and video.strip():
        try:
            video_filter = normalize_video_number(video)
        except HTTPException:
            video_filter = None
    else:
        video_filter = None

    if isinstance(status, str) and status.strip() and status.strip().lower() != "all":
        status_filter = status.strip()
    else:
        status_filter = None

    channel_info_map = refresh_channel_info()
    if channel_filter:
        channel_dirs = [DATA_ROOT / channel_filter]
    else:
        channel_dirs = list_channel_dirs()

    items: List[AudioReviewItemResponse] = []

    for channel_dir in channel_dirs:
        if not channel_dir.is_dir():
            continue
        channel_code = channel_dir.name.upper()
        channel_title = _resolve_channel_title(channel_code, channel_info_map)

        for video_dir in list_video_dirs(channel_code):
            video_number = video_dir.name
            if video_filter and video_number != video_filter:
                continue
            try:
                status_payload = load_status(channel_code, video_number)
            except HTTPException as exc:
                if exc.status_code == 404:
                    continue
                raise

            top_level_status = status_payload.get("status", "unknown")
            if status_filter and top_level_status != status_filter:
                continue

            metadata = status_payload.get("metadata", {}) or {}
            title = metadata.get("sheet_title") or metadata.get("title") or status_payload.get("script_id")

            stages = status_payload.get("stages", {}) or {}
            audio_stage_meta = stages.get("audio_synthesis", {}) or {}
            subtitle_stage_meta = stages.get("srt_generation", {}) or {}

            audio_stage = str(audio_stage_meta.get("status", "pending"))
            audio_stage_updated_at = audio_stage_meta.get("updated_at")
            subtitle_stage = str(subtitle_stage_meta.get("status", "pending"))
            subtitle_stage_updated_at = subtitle_stage_meta.get("updated_at")

            base_dir = video_base_dir(channel_code, video_number)
            audio_path = resolve_audio_path(status_payload, base_dir)
            audio_duration = get_audio_duration_seconds(audio_path) if audio_path else None
            audio_updated_at: Optional[str] = None
            if audio_path:
                try:
                    audio_updated_at = (
                        datetime.fromtimestamp(audio_path.stat().st_mtime, timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                except OSError:
                    audio_updated_at = None

            audio_url = f"/api/channels/{channel_code}/videos/{video_number}/audio" if audio_path else None

            # SRT path (for UI download/display)
            srt_path = resolve_srt_path(status_payload, base_dir)
            srt_url = f"/api/channels/{channel_code}/videos/{video_number}/srt" if srt_path else None
            log_path = resolve_log_path(status_payload, base_dir)
            log_url = f"/api/channels/{channel_code}/videos/{video_number}/log" if log_path else None

            audio_meta = metadata.get("audio") if isinstance(metadata.get("audio"), dict) else metadata.get("audio", {})
            if not isinstance(audio_meta, dict):
                audio_meta = {}
            engine_meta = audio_meta.get("engine") if isinstance(audio_meta, dict) else None
            log_summary = summarize_log(log_path) if log_path else None

            quality_meta = audio_meta.get("quality")
            audio_quality_status = None
            audio_quality_summary = None
            if isinstance(quality_meta, dict):
                audio_quality_status = quality_meta.get("status") or quality_meta.get("label")
                audio_quality_summary = quality_meta.get("summary") or quality_meta.get("note")
            elif isinstance(quality_meta, str):
                audio_quality_status = quality_meta

            waveform_meta = audio_meta.get("waveform")
            audio_waveform_image = None
            audio_waveform_url = None
            if isinstance(waveform_meta, dict):
                audio_waveform_image = waveform_meta.get("image")
                audio_waveform_url = waveform_meta.get("url")
            elif isinstance(waveform_meta, str):
                audio_waveform_url = waveform_meta

            audio_message = audio_meta.get("message") if isinstance(audio_meta.get("message"), str) else None
            audio_error = audio_meta.get("error") if isinstance(audio_meta.get("error"), str) else None
            manual_pause_count = _count_manual_pauses(audio_meta.get("history"))

            # 再生成用の input_path を明示。script_audio_path → tts_path → assembled_path の優先度で拾う。
            input_path = None
            for cand in (
                status_payload.get("script_audio_path"),
                status_payload.get("tts_path"),
                status_payload.get("assembled_path"),
            ):
                if cand:
                    input_path = str(base_dir / cand)
                    break
            # メタに明示されていればそれを優先
            if isinstance(audio_meta.get("input_path"), str):
                input_path = str(base_dir / audio_meta.get("input_path"))

            ready_for_audio = bool(metadata.get("ready_for_audio", False))

            items.append(
                AudioReviewItemResponse(
                    channel=channel_code,
                    video=video_number,
                    status=top_level_status,
                    title=title,
                    channel_title=channel_title,
                    workspace_path=f"/channels/{channel_code}/videos/{video_number}?tab=audio",
                    audio_stage=audio_stage,
                    audio_stage_updated_at=audio_stage_updated_at,
                    subtitle_stage=subtitle_stage,
                    subtitle_stage_updated_at=subtitle_stage_updated_at,
                    audio_quality_status=audio_quality_status,
                    audio_quality_summary=audio_quality_summary,
                    audio_updated_at=audio_updated_at,
                    audio_duration_seconds=audio_duration,
                    audio_url=audio_url,
                    srt_url=srt_url,
                    audio_waveform_image=audio_waveform_image,
                    audio_waveform_url=audio_waveform_url,
                    audio_message=audio_message,
                    audio_error=audio_error,
                    manual_pause_count=manual_pause_count or None,
                    ready_for_audio=ready_for_audio,
                    tts_input_path=input_path,
                    audio_log_url=log_url,
                    audio_engine=engine_meta,
                    audio_log_summary=log_summary,
                )
            )

    items.sort(key=lambda item: item.audio_updated_at or "", reverse=True)
    return items


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


@app.get(
    "/api/workspaces/thumbnails/image-models",
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
    pricing_by_id, fetched_at = _get_openrouter_pricing_by_model_id()
    if fetched_at:
        pricing_updated_at = datetime.fromtimestamp(fetched_at, timezone.utc).isoformat()

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


@app.get(
    "/api/workspaces/thumbnails/{channel}/templates",
    response_model=ThumbnailChannelTemplatesResponse,
)
def get_thumbnail_channel_templates(channel: str):
    channel_code = normalize_channel_code(channel)
    with THUMBNAIL_TEMPLATES_LOCK:
        _, payload = _load_thumbnail_templates_document()
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


@app.get(
    "/api/workspaces/thumbnails/{channel}/layer-specs",
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


@app.get(
    "/api/workspaces/thumbnails/{channel}/{video}/layer-specs",
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


@app.get(
    "/api/workspaces/thumbnails/param-catalog",
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


@app.get(
    "/api/workspaces/thumbnails/{channel}/{video}/thumb-spec",
    response_model=ThumbnailThumbSpecResponse,
)
def get_thumbnail_thumb_spec(
    channel: str,
    video: str,
    stable: Optional[str] = Query(None, description="stable output id (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    try:
        from script_pipeline.thumbnails.thumb_spec import (
            THUMB_SPEC_SCHEMA_V1,
            ThumbSpecLoadResult,
            load_thumb_spec,
            validate_thumb_spec_payload,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"thumb_spec module is not available: {exc}") from exc

    stable_id = _normalize_thumbnail_stable_id(stable if stable is not None else variant)
    loaded = None
    stable_exists = False
    stable_path = None
    if stable_id:
        stable_path = _thumb_spec_stable_path(channel_code, video_number, stable_id)
        stable_exists = stable_path.exists()
        if stable_exists:
            try:
                payload = json.loads(stable_path.read_text(encoding="utf-8"))
                validated = validate_thumb_spec_payload(payload, channel=channel_code, video=video_number)
                loaded = ThumbSpecLoadResult(payload=validated, path=stable_path)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"failed to load thumb_spec: {exc}") from exc
    if loaded is None:
        # Stable variants must not inherit thumb_spec.json implicitly.
        # Only the primary stable (00_thumb_1) may fall back to legacy thumb_spec.json.
        if not stable_id or stable_id == "00_thumb_1":
            loaded = load_thumb_spec(channel_code, video_number)
    if loaded is None:
        target_path = stable_path if stable_path is not None else None
        return ThumbnailThumbSpecResponse(
            exists=False if stable_id else False,
            path=(safe_relative_path(target_path) if isinstance(target_path, Path) else None),
            schema=THUMB_SPEC_SCHEMA_V1,
            channel=channel_code,
            video=video_number,
            overrides={},
            updated_at=None,
            normalized_overrides_leaf={},
        )

    payload = loaded.payload if isinstance(loaded.payload, dict) else {}
    overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else {}
    updated_at = payload.get("updated_at") if isinstance(payload.get("updated_at"), str) else None
    normalized_leaf = (
        payload.get("_normalized_overrides_leaf") if isinstance(payload.get("_normalized_overrides_leaf"), dict) else {}
    )

    return ThumbnailThumbSpecResponse(
        exists=stable_exists if stable_id else True,
        path=(
            safe_relative_path(stable_path) or str(stable_path)
            if stable_id and isinstance(stable_path, Path)
            else (safe_relative_path(loaded.path) or str(loaded.path))
        ),
        schema=(str(payload.get("schema") or "") or None),
        channel=channel_code,
        video=video_number,
        overrides=overrides,
        updated_at=updated_at,
        normalized_overrides_leaf=normalized_leaf,
    )


@app.put(
    "/api/workspaces/thumbnails/{channel}/{video}/thumb-spec",
    response_model=ThumbnailThumbSpecResponse,
)
def upsert_thumbnail_thumb_spec(
    channel: str,
    video: str,
    request: ThumbnailThumbSpecUpdateRequest,
    stable: Optional[str] = Query(None, description="stable output id (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    try:
        from script_pipeline.thumbnails.thumb_spec import THUMB_SPEC_SCHEMA_V1, save_thumb_spec, validate_thumb_spec_payload
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"thumb_spec module is not available: {exc}") from exc

    overrides = request.overrides if isinstance(request.overrides, dict) else {}
    try:
        stable_id = _normalize_thumbnail_stable_id(stable if stable is not None else variant)
        if not stable_id:
            save_thumb_spec(channel_code, video_number, overrides)
        else:
            path = _thumb_spec_stable_path(channel_code, video_number, stable_id)
            payload = {
                "schema": THUMB_SPEC_SCHEMA_V1,
                "channel": channel_code,
                "video": video_number,
                "overrides": overrides,
                "updated_at": _utc_now_iso_z(),
            }
            validated = validate_thumb_spec_payload(payload, channel=channel_code, video=video_number)
            write_payload = {
                "schema": THUMB_SPEC_SCHEMA_V1,
                "channel": channel_code,
                "video": video_number,
                "overrides": overrides,
                "updated_at": validated.get("updated_at") or _utc_now_iso_z(),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(write_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(path)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to save thumb_spec: {exc}") from exc

    stable_id = _normalize_thumbnail_stable_id(stable if stable is not None else variant)
    if stable_id:
        return get_thumbnail_thumb_spec(channel_code, video_number, stable=stable_id)
    return get_thumbnail_thumb_spec(channel_code, video_number)


@app.get(
    "/api/workspaces/thumbnails/{channel}/{video}/text-line-spec",
    response_model=ThumbnailTextLineSpecResponse,
)
def get_thumbnail_text_line_spec(
    channel: str,
    video: str,
    stable: Optional[str] = Query(None, description="stable output id (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    stable_raw = stable if stable is not None else variant
    stable_id = _normalize_thumbnail_stable_id(stable_raw) if stable_raw else None
    stable_label = stable_id or "default"
    legacy_path = THUMBNAIL_ASSETS_DIR / channel_code / video_number / "text_line_spec.json"
    stable_path = _text_line_spec_stable_path(channel_code, video_number, stable_id) if stable_id else None
    candidates: List[Path] = []
    if stable_path is not None:
        candidates.append(stable_path)
        # Stable variants must not inherit legacy implicitly.
        # Only the primary stable (00_thumb_1) may fall back to legacy text_line_spec.json.
        if stable_id == "00_thumb_1":
            candidates.append(legacy_path)
    else:
        candidates.append(legacy_path)

    path: Optional[Path] = None
    for candidate in candidates:
        if candidate.exists():
            path = candidate
            break

    if path is None:
        target_path = stable_path if stable_path is not None else legacy_path
        return ThumbnailTextLineSpecResponse(
            exists=False,
            path=(safe_relative_path(target_path) or str(target_path)),
            channel=channel_code,
            video=video_number,
            stable=stable_label,
            lines={},
            updated_at=None,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to load text_line_spec: {exc}") from exc
    lines_payload = payload.get("lines") if isinstance(payload, dict) else None
    lines: Dict[str, ThumbnailTextLineSpecLinePayload] = {}
    if isinstance(lines_payload, dict):
        for raw_slot, raw_line in lines_payload.items():
            if not isinstance(raw_slot, str) or not raw_slot.strip():
                continue
            if not isinstance(raw_line, dict):
                continue
            try:
                lines[raw_slot.strip()] = ThumbnailTextLineSpecLinePayload(
                    offset_x=float(raw_line.get("offset_x", 0.0)),
                    offset_y=float(raw_line.get("offset_y", 0.0)),
                    scale=float(raw_line.get("scale", 1.0)),
                    rotate_deg=float(raw_line.get("rotate_deg", 0.0)),
                )
            except Exception:
                continue
    updated_at = payload.get("updated_at") if isinstance(payload, dict) and isinstance(payload.get("updated_at"), str) else None
    return ThumbnailTextLineSpecResponse(
        exists=True,
        path=(safe_relative_path(path) or str(path)),
        channel=channel_code,
        video=video_number,
        stable=stable_label,
        lines=lines,
        updated_at=updated_at,
    )


@app.put(
    "/api/workspaces/thumbnails/{channel}/{video}/text-line-spec",
    response_model=ThumbnailTextLineSpecResponse,
)
def upsert_thumbnail_text_line_spec(
    channel: str,
    video: str,
    request: ThumbnailTextLineSpecUpdateRequest,
    stable: Optional[str] = Query(None, description="stable output id (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    stable_raw = stable if stable is not None else variant
    stable_id = _normalize_thumbnail_stable_id(stable_raw) if stable_raw else None
    stable_label = stable_id or "default"

    lines_out: Dict[str, Dict[str, float]] = {}
    for raw_slot, raw_line in (request.lines or {}).items():
        if not isinstance(raw_slot, str) or not raw_slot.strip():
            continue
        slot_key = raw_slot.strip()
        if isinstance(raw_line, ThumbnailTextLineSpecLinePayload):
            ox = float(raw_line.offset_x)
            oy = float(raw_line.offset_y)
            sc = float(raw_line.scale)
            rot = float(raw_line.rotate_deg)
        elif isinstance(raw_line, dict):
            try:
                ox = float(raw_line.get("offset_x", 0.0))
                oy = float(raw_line.get("offset_y", 0.0))
                sc = float(raw_line.get("scale", 1.0))
                rot = float(raw_line.get("rotate_deg", 0.0))
            except Exception:
                continue
        else:
            continue
        sc = max(0.25, min(4.0, sc))
        rot = max(-180.0, min(180.0, rot))
        lines_out[slot_key] = {"offset_x": ox, "offset_y": oy, "scale": sc, "rotate_deg": rot}

    payload = {
        "schema": THUMBNAIL_TEXT_LINE_SPEC_SCHEMA_V1,
        "channel": channel_code,
        "video": video_number,
        "stable": stable_label,
        "lines": lines_out,
        "updated_at": _utc_now_iso_z(),
    }
    path = (
        _text_line_spec_stable_path(channel_code, video_number, stable_id)
        if stable_id
        else (THUMBNAIL_ASSETS_DIR / channel_code / video_number / "text_line_spec.json")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    if stable_id:
        return get_thumbnail_text_line_spec(channel_code, video_number, stable=stable_id)
    return get_thumbnail_text_line_spec(channel_code, video_number, stable="")


@app.get(
    "/api/workspaces/thumbnails/{channel}/{video}/elements-spec",
    response_model=ThumbnailElementsSpecResponse,
)
def get_thumbnail_elements_spec(
    channel: str,
    video: str,
    stable: Optional[str] = Query(None, description="stable output id (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    stable_raw = stable if stable is not None else variant
    stable_id = _normalize_thumbnail_stable_id(stable_raw) if stable_raw else None
    stable_label = stable_id or "default"
    legacy_path = THUMBNAIL_ASSETS_DIR / channel_code / video_number / "elements_spec.json"
    stable_path = _elements_spec_stable_path(channel_code, video_number, stable_id) if stable_id else None
    candidates: List[Path] = []
    if stable_path is not None:
        candidates.append(stable_path)
        # Stable variants must not inherit legacy implicitly.
        # Only the primary stable (00_thumb_1) may fall back to legacy elements_spec.json.
        if stable_id == "00_thumb_1":
            candidates.append(legacy_path)
    else:
        candidates.append(legacy_path)

    path: Optional[Path] = None
    for candidate in candidates:
        if candidate.exists():
            path = candidate
            break

    if path is None:
        target_path = stable_path if stable_path is not None else legacy_path
        return ThumbnailElementsSpecResponse(
            exists=False,
            path=(safe_relative_path(target_path) or str(target_path)),
            channel=channel_code,
            video=video_number,
            stable=stable_label,
            elements=[],
            updated_at=None,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to load elements_spec: {exc}") from exc
    elements_payload = payload.get("elements") if isinstance(payload, dict) else None
    elements: List[ThumbnailElementPayload] = []
    if isinstance(elements_payload, list):
        for raw in elements_payload:
            if not isinstance(raw, dict):
                continue
            try:
                elements.append(ThumbnailElementPayload(**raw))
            except Exception:
                continue
    updated_at = (
        payload.get("updated_at") if isinstance(payload, dict) and isinstance(payload.get("updated_at"), str) else None
    )
    return ThumbnailElementsSpecResponse(
        exists=True,
        path=(safe_relative_path(path) or str(path)),
        channel=channel_code,
        video=video_number,
        stable=stable_label,
        elements=elements,
        updated_at=updated_at,
    )


@app.put(
    "/api/workspaces/thumbnails/{channel}/{video}/elements-spec",
    response_model=ThumbnailElementsSpecResponse,
)
def upsert_thumbnail_elements_spec(
    channel: str,
    video: str,
    request: ThumbnailElementsSpecUpdateRequest,
    stable: Optional[str] = Query(None, description="stable output id (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    stable_raw = stable if stable is not None else variant
    stable_id = _normalize_thumbnail_stable_id(stable_raw) if stable_raw else None
    stable_label = stable_id or "default"

    allowed_kinds = {"rect", "circle", "image"}
    allowed_layers = {"above_portrait", "below_portrait"}
    elements_out: List[Dict[str, Any]] = []
    for raw in request.elements or []:
        try:
            element = raw if isinstance(raw, ThumbnailElementPayload) else ThumbnailElementPayload(**(raw or {}))
        except Exception:
            continue
        element_id = str(element.id or "").strip()
        if not element_id:
            continue
        kind = str(element.kind or "").strip().lower()
        if kind not in allowed_kinds:
            continue
        layer_label = str(element.layer or "").strip()
        layer_label = layer_label if layer_label in allowed_layers else "above_portrait"
        try:
            z = int(element.z)
        except Exception:
            z = 0
        try:
            x = float(element.x)
            y = float(element.y)
            w = float(element.w)
            h = float(element.h)
            rotation_deg = float(element.rotation_deg)
            opacity = float(element.opacity)
        except Exception:
            continue
        # Allow moving elements far outside the canvas (pasteboard-style editing).
        x = max(-5.0, min(6.0, x))
        y = max(-5.0, min(6.0, y))
        w = max(0.01, min(4.0, w))
        h = max(0.01, min(4.0, h))
        rotation_deg = max(-180.0, min(180.0, rotation_deg))
        opacity = max(0.0, min(1.0, opacity))

        fill = str(element.fill or "").strip() or None
        src_path = str(element.src_path or "").strip() or None
        if src_path:
            rel = Path(src_path)
            if rel.is_absolute() or any(part == ".." for part in rel.parts):
                src_path = None
        stroke_payload = None
        if element.stroke is not None:
            try:
                stroke = element.stroke if isinstance(element.stroke, ThumbnailElementStrokePayload) else None
                stroke_color = str((stroke.color if stroke else None) or "").strip() or None
                stroke_width = float(stroke.width_px if stroke else 0.0)
                stroke_width = max(0.0, min(256.0, stroke_width))
                if stroke_color or stroke_width:
                    stroke_payload = {"color": stroke_color, "width_px": stroke_width}
            except Exception:
                stroke_payload = None
        if kind == "image" and not src_path:
            # Image elements must have a source.
            continue
        if kind in {"rect", "circle"} and not fill:
            fill = "#ffffff"

        out: Dict[str, Any] = {
            "id": element_id,
            "kind": kind,
            "layer": layer_label,
            "z": z,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "rotation_deg": rotation_deg,
            "opacity": opacity,
        }
        if fill:
            out["fill"] = fill
        if stroke_payload:
            out["stroke"] = stroke_payload
        if src_path:
            out["src_path"] = src_path
        elements_out.append(out)

    payload = {
        "schema": THUMBNAIL_ELEMENTS_SPEC_SCHEMA_V1,
        "channel": channel_code,
        "video": video_number,
        "stable": stable_label,
        "elements": elements_out,
        "updated_at": _utc_now_iso_z(),
    }
    path = (
        _elements_spec_stable_path(channel_code, video_number, stable_id)
        if stable_id
        else (THUMBNAIL_ASSETS_DIR / channel_code / video_number / "elements_spec.json")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    if stable_id:
        return get_thumbnail_elements_spec(channel_code, video_number, stable=stable_id)
    return get_thumbnail_elements_spec(channel_code, video_number, stable="")


@app.get(
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


@app.post(
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


@app.post(
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


@app.post(
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


@app.post(
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


@app.post(
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


@app.put(
    "/api/workspaces/thumbnails/{channel}/templates",
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


@app.get("/api/workspaces/thumbnails", response_model=ThumbnailOverviewResponse)
def get_thumbnail_overview():
    projects_path = _resolve_thumbnail_projects_path()

    try:
        with projects_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="thumbnail projects file not found")
    except json.JSONDecodeError as exc:  # pragma: no cover - unexpected file mutation
        raise HTTPException(status_code=500, detail=f"invalid thumbnail projects payload: {exc}") from exc

    projects_payload = payload.get("projects") or []
    channel_info_map = refresh_channel_info()

    channel_map: Dict[str, Dict[str, Any]] = {}

    for raw_project in projects_payload:
        channel_code = str(raw_project.get("channel") or "").upper()
        video_code = str(raw_project.get("video") or "").strip()
        if not channel_code or not video_code:
            continue

        entry = channel_map.setdefault(
            channel_code,
            {"projects": [], "videos": []},
        )

        selected_variant_id = raw_project.get("selected_variant_id")
        variants_payload = raw_project.get("variants") or []
        variants: List[ThumbnailVariantResponse] = []

        for raw_variant in variants_payload:
            variant_id = str(raw_variant.get("id") or "").strip()
            if not variant_id:
                continue
            tags_payload = raw_variant.get("tags")
            tags_list = (
                [tag for tag in tags_payload if isinstance(tag, str)]
                if isinstance(tags_payload, list)
                else None
            )
            variants.append(
                ThumbnailVariantResponse(
                    id=variant_id,
                    label=raw_variant.get("label"),
                    status=raw_variant.get("status") or "draft",
                    image_url=raw_variant.get("image_url"),
                    image_path=raw_variant.get("image_path"),
                    preview_url=raw_variant.get("preview_url"),
                    notes=raw_variant.get("notes"),
                    tags=(tags_list or None),
                    provider=raw_variant.get("provider"),
                    model=raw_variant.get("model"),
                    model_key=raw_variant.get("model_key"),
                    openrouter_generation_id=raw_variant.get("openrouter_generation_id"),
                    cost_usd=raw_variant.get("cost_usd"),
                    usage=raw_variant.get("usage"),
                    is_selected=selected_variant_id == variant_id,
                    created_at=raw_variant.get("created_at"),
                    updated_at=raw_variant.get("updated_at"),
                )
            )

        tags_payload = raw_project.get("tags")
        project_tags = (
            [tag for tag in tags_payload if isinstance(tag, str)]
            if isinstance(tags_payload, list)
            else None
        )

        entry["projects"].append(
            ThumbnailProjectResponse(
                channel=channel_code,
                video=video_code,
                script_id=raw_project.get("script_id"),
                title=raw_project.get("title"),
                sheet_title=raw_project.get("sheet_title"),
                status=raw_project.get("status") or "draft",
                owner=raw_project.get("owner"),
                summary=raw_project.get("summary"),
                notes=raw_project.get("notes"),
                tags=(project_tags or None),
                variants=variants,
                ready_for_publish=raw_project.get("ready_for_publish"),
                updated_at=raw_project.get("updated_at"),
                status_updated_at=raw_project.get("status_updated_at"),
                due_at=raw_project.get("due_at"),
                selected_variant_id=selected_variant_id,
                audio_stage=raw_project.get("audio_stage"),
                script_stage=raw_project.get("script_stage"),
            )
        )

    def _safe_int(value: Any) -> Optional[int]:
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.strip():
            try:
                return int(float(value))
            except ValueError:
                return None
        return None

    remaining_refresh_budget = max(0, YOUTUBE_UPLOADS_MAX_REFRESH_PER_REQUEST)
    merged_channels: set[str] = set()

    for channel_code, info in channel_info_map.items():
        entry = channel_map.setdefault(channel_code, {"projects": [], "videos": []})
        if channel_code not in merged_channels:
            _merge_disk_thumbnail_variants(channel_code, entry)
            merged_channels.add(channel_code)
        branding = info.get("branding") if isinstance(info.get("branding"), dict) else {}
        youtube_meta = info.get("youtube") if isinstance(info.get("youtube"), dict) else {}

        summary = ThumbnailChannelSummaryResponse(
            total=len(entry.get("projects", [])),
            subscriber_count=_safe_int(branding.get("subscriber_count") or youtube_meta.get("subscriber_count")),
            view_count=_safe_int(branding.get("view_count") or youtube_meta.get("view_count")),
            video_count=_safe_int(branding.get("video_count") or youtube_meta.get("video_count")),
        )
        entry["summary"] = summary

        channel_cache_key = channel_code.lower()
        now = datetime.now(timezone.utc)
        cached_timestamp: Optional[datetime] = None
        cached_videos: List[ThumbnailChannelVideoResponse] = []

        backoff_until = YOUTUBE_UPLOAD_FAILURE_STATE.get(channel_cache_key)
        if backoff_until and backoff_until <= now:
            YOUTUBE_UPLOAD_FAILURE_STATE.pop(channel_cache_key, None)
            backoff_until = None

        cache_entry = YOUTUBE_UPLOAD_CACHE.get(channel_cache_key)
        if cache_entry:
            cached_timestamp, cached_videos = cache_entry
        else:
            cached_timestamp, cached_videos = _load_cached_uploads(channel_cache_key)
            if cached_videos:
                YOUTUBE_UPLOAD_CACHE[channel_cache_key] = (cached_timestamp or now, cached_videos)

        videos: List[ThumbnailChannelVideoResponse] = []
        cache_is_fresh = False
        if cached_timestamp and (now - cached_timestamp) < YOUTUBE_UPLOAD_CACHE_TTL:
            videos = list(cached_videos)
            cache_is_fresh = True

        backoff_active = bool(backoff_until and backoff_until > now)
        should_refresh = (
            not cache_is_fresh
            and YOUTUBE_CLIENT
            and youtube_meta.get("channel_id")
            and remaining_refresh_budget > 0
            and not backoff_active
        )

        if should_refresh:
            try:
                uploads = YOUTUBE_CLIENT.fetch_recent_uploads(youtube_meta["channel_id"], max_results=6)

                def _item_value(obj: Any, key: str) -> Optional[Any]:
                    if hasattr(obj, key):
                        return getattr(obj, key)
                    if isinstance(obj, dict):
                        return obj.get(key)
                    return None

                videos = []
                for item in uploads:
                    video_id = _item_value(item, "video_id")
                    url = _item_value(item, "url")
                    title = _item_value(item, "title")
                    if not video_id or not url or not title:
                        continue
                    videos.append(
                        ThumbnailChannelVideoResponse(
                            video_id=video_id,
                            title=title,
                            url=url,
                            thumbnail_url=_item_value(item, "thumbnail_url"),
                            published_at=_item_value(item, "published_at"),
                            view_count=_safe_int(_item_value(item, "view_count")),
                            duration_seconds=_item_value(item, "duration_seconds"),
                            source="youtube",
                        )
                    )
                fetched_at = now
                YOUTUBE_UPLOAD_CACHE[channel_cache_key] = (fetched_at, videos)
                _save_cached_uploads(channel_cache_key, fetched_at, videos)
                cache_is_fresh = True
                remaining_refresh_budget = max(0, remaining_refresh_budget - 1)
            except YouTubeDataAPIError as exc:  # pragma: no cover - API failure
                logger.warning("Failed to fetch YouTube uploads for %s: %s", channel_code, exc)
                remaining_refresh_budget = max(0, remaining_refresh_budget - 1)
                error_message = str(exc).lower()
                if "quota" in error_message or "useratelimitexceeded" in error_message:
                    YOUTUBE_UPLOAD_FAILURE_STATE[channel_cache_key] = now + YOUTUBE_UPLOAD_BACKOFF
                if cached_videos:
                    videos = list(cached_videos)
        elif backoff_active:
            logger.info(
                "Skipping YouTube refresh for %s due to quota backoff until %s",
                channel_code,
                backoff_until.isoformat() if backoff_until else "?",
            )
        elif remaining_refresh_budget <= 0 and not cache_is_fresh:
            logger.debug("Refresh budget exhausted for thumbnails; using cached entries for %s", channel_code)

        if not videos and cached_videos:
            videos = list(cached_videos)

        entry["videos"] = videos

    overview_channels: List[ThumbnailChannelBlockResponse] = []

    for channel_code, entry in sorted(channel_map.items()):
        primary_library = _channel_primary_library_dir(channel_code)
        if primary_library.exists():
            try:
                library_path = str(primary_library.relative_to(PROJECT_ROOT))
            except ValueError:
                library_path = str(primary_library)
        else:
            library_path = None
        if channel_code not in merged_channels:
            _merge_disk_thumbnail_variants(channel_code, entry)
            merged_channels.add(channel_code)
        summary_obj = entry.get("summary")
        if summary_obj is None:
            summary_obj = ThumbnailChannelSummaryResponse(
                total=len(entry.get("projects", [])),
            )
        overview_channels.append(
            ThumbnailChannelBlockResponse(
                channel=channel_code,
                channel_title=_resolve_channel_title(channel_code, channel_info_map),
                summary=summary_obj,
                projects=entry.get("projects", []),
                videos=entry.get("videos", []),
                library_path=library_path,
            )
        )

    return ThumbnailOverviewResponse(
        generated_at=payload.get("updated_at"),
        channels=overview_channels,
    )


@app.patch("/api/workspaces/thumbnails/{channel}/{video}", response_model=Dict[str, str])
def update_thumbnail_project(channel: str, video: str, payload: ThumbnailProjectUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return {"status": "ok"}
    now = datetime.now(timezone.utc).isoformat()
    with THUMBNAIL_PROJECTS_LOCK:
        path, document = _load_thumbnail_projects_document()
        project = _get_or_create_thumbnail_project(document, channel_code, video_number)

        def _apply_text(field: str, value: Optional[str]) -> None:
            if value is None:
                project.pop(field, None)
            else:
                text = value.strip()
                if text:
                    project[field] = text
                else:
                    project.pop(field, None)

        for field in ("owner", "summary", "notes", "due_at"):
            if field in updates:
                _apply_text(field, updates.get(field))

        if "tags" in updates:
            normalized_tags = _normalize_thumbnail_tags(updates.get("tags"))
            if normalized_tags:
                project["tags"] = normalized_tags
            else:
                project.pop("tags", None)

        if "status" in updates:
            project["status"] = _normalize_thumbnail_status(updates.get("status"))
            project["status_updated_at"] = now

        if "selected_variant_id" in updates:
            variant_id = updates.get("selected_variant_id")
            if variant_id:
                project["selected_variant_id"] = variant_id
            else:
                project.pop("selected_variant_id", None)

        project["updated_at"] = now
        _write_thumbnail_projects_document(path, document)
    return {"status": "ok"}


@app.post(
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


@app.patch(
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


@app.post(
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


@app.post(
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


@app.post(
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


@app.post(
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


@app.post(
    "/api/workspaces/thumbnails/{channel}/library/upload",
    response_model=List[ThumbnailLibraryAssetResponse],
)
async def upload_thumbnail_library_assets(channel: str, files: List[UploadFile] = File(...)):
    channel_code = normalize_channel_code(channel)
    if not files:
        raise HTTPException(status_code=400, detail="アップロードする画像を選択してください。")
    library_dir = _channel_primary_library_dir(channel_code, ensure=True)
    assets: List[ThumbnailLibraryAssetResponse] = []
    for file in files:
        if not file.filename:
            continue
        sanitized = _sanitize_library_filename(file.filename, default_prefix="library_asset")
        destination = _ensure_unique_filename(library_dir, sanitized)
        await _save_upload_file(file, destination)
        assets.append(_build_library_asset_response(channel_code, destination, base_dir=library_dir))
    if not assets:
        raise HTTPException(status_code=400, detail="有効な画像ファイルがありませんでした。")
    return assets


@app.post(
    "/api/workspaces/thumbnails/{channel}/library/import",
    response_model=ThumbnailLibraryAssetResponse,
)
def import_thumbnail_library_asset(channel: str, payload: ThumbnailLibraryImportRequest):
    channel_code = normalize_channel_code(channel)
    library_dir = _channel_primary_library_dir(channel_code, ensure=True)
    source_url = payload.url.strip()
    if not source_url:
        raise HTTPException(status_code=400, detail="URL を指定してください。")
    try:
        response = requests.get(source_url, timeout=THUMBNAIL_REMOTE_FETCH_TIMEOUT)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"画像の取得に失敗しました: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"画像の取得に失敗しました (status {response.status_code})")
    content = response.content
    if not content:
        raise HTTPException(status_code=400, detail="画像データが空です。")
    if len(content) > THUMBNAIL_LIBRARY_MAX_BYTES:
        raise HTTPException(status_code=400, detail="画像サイズが大きすぎます。")
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    suffix = mimetypes.guess_extension(content_type) if content_type else None
    if suffix == ".jpe":
        suffix = ".jpg"
    if suffix not in THUMBNAIL_SUPPORTED_EXTENSIONS:
        suffix = None
    candidate_name = payload.file_name.strip() if payload.file_name else ""
    if not candidate_name:
        parsed = urllib.parse.urlparse(source_url)
        candidate_name = Path(parsed.path).name or ""
    if candidate_name:
        sanitized = _sanitize_library_filename(candidate_name, default_prefix="imported")
        if suffix and not sanitized.lower().endswith(suffix):
            sanitized = f"{Path(sanitized).stem}{suffix}"
    else:
        sanitized = _sanitize_library_filename(f"imported{suffix or '.png'}", default_prefix="imported")
    destination = _ensure_unique_filename(library_dir, sanitized)
    try:
        with destination.open("wb") as buffer:
            buffer.write(content)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"画像の保存に失敗しました: {exc}") from exc
    return _build_library_asset_response(channel_code, destination, base_dir=library_dir)


@app.get(
    "/api/workspaces/thumbnails/{channel}/library",
    response_model=List[ThumbnailLibraryAssetResponse],
)
def get_thumbnail_library(channel: str):
    channel_code = normalize_channel_code(channel)
    return _list_channel_thumbnail_library(channel_code)

@app.patch(
    "/api/workspaces/thumbnails/{channel}/library/{asset_name}",
    response_model=ThumbnailLibraryAssetResponse,
)
def rename_thumbnail_library_asset(
    channel: str, asset_name: str, payload: ThumbnailLibraryRenameRequest
):
    channel_code = normalize_channel_code(channel)
    base_dir, current_path = _resolve_library_asset_path(channel_code, asset_name)
    new_name = payload.new_name
    destination = base_dir / new_name
    if destination.exists():
        raise HTTPException(status_code=409, detail="同名のファイルが既に存在します。")
    try:
        current_path.rename(destination)
    except OSError as exc:  # pragma: no cover - filesystem failure
        raise HTTPException(status_code=500, detail=f"ファイル名の変更に失敗しました: {exc}") from exc
    return _build_library_asset_response(channel_code, destination)


@app.delete(
    "/api/workspaces/thumbnails/{channel}/library/{asset_path:path}",
    status_code=204,
    response_class=PlainTextResponse,
)
def delete_thumbnail_library_asset(channel: str, asset_path: str):
    channel_code = normalize_channel_code(channel)
    _, source_path = _resolve_library_asset_path(channel_code, asset_path)
    try:
        source_path.unlink()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="asset not found")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"ファイルの削除に失敗しました: {exc}") from exc
    return PlainTextResponse("", status_code=204)


@app.post(
    "/api/workspaces/thumbnails/{channel}/library/{asset_name}/assign",
    response_model=ThumbnailLibraryAssignResponse,
)
def assign_thumbnail_library_asset(
    channel: str,
    asset_name: str,
    payload: ThumbnailLibraryAssignRequest,
):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(payload.video)
    _, source_path = _resolve_library_asset_path(channel_code, asset_name)
    image_path, public_url = _copy_library_asset_to_video(channel_code, video_number, source_path)
    label = payload.label.strip() if payload.label else Path(source_path.name).stem
    _persist_thumbnail_variant(
        channel_code,
        video_number,
        label=label,
        status="draft",
        image_path=image_path,
        make_selected=bool(payload.make_selected),
    )
    _append_thumbnail_quick_history(
        {
            "channel": channel_code,
            "video": video_number,
            "label": label or None,
            "asset_name": source_path.name,
            "image_path": image_path,
            "public_url": public_url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    return ThumbnailLibraryAssignResponse(
        file_name=source_path.name,
        image_path=image_path,
        public_url=public_url,
    )


@app.get(
    "/api/workspaces/thumbnails/history",
    response_model=List[ThumbnailQuickHistoryEntry],
)
def get_thumbnail_quick_history(
    channel: Optional[str] = Query(None, description="CHコード（例: CH06）"),
    limit: int = Query(20, ge=1, le=200),
):
    channel_code = normalize_channel_code(channel) if channel else None
    return _read_thumbnail_quick_history(channel_code, limit)


@app.get("/api/workspaces/thumbnails/{channel}/download.zip")
def download_thumbnail_zip(
    channel: str,
    mode: str = Query("selected", description="selected | all | two_up"),
):
    channel_code = normalize_channel_code(channel)
    mode_norm = (mode or "selected").strip().lower()
    if mode_norm not in {"selected", "all", "two_up"}:
        raise HTTPException(status_code=400, detail="mode must be 'selected', 'all', or 'two_up'")

    projects_path = _resolve_thumbnail_projects_path()
    try:
        with projects_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="thumbnail projects file not found")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"invalid thumbnail projects payload: {exc}") from exc

    projects_payload = payload.get("projects") or []
    files: List[Tuple[str, Path]] = []
    used_names: set[str] = set()

    for raw_project in projects_payload:
        if not isinstance(raw_project, dict):
            continue
        if str(raw_project.get("channel") or "").strip().upper() != channel_code:
            continue
        video_number = _coerce_video_from_dir(str(raw_project.get("video") or ""))
        if not video_number:
            continue

        variants_payload = raw_project.get("variants") or []
        if not isinstance(variants_payload, list) or not variants_payload:
            continue

        selected_id = str(raw_project.get("selected_variant_id") or "").strip()
        selected_variant: Optional[dict] = None
        if selected_id:
            for v in variants_payload:
                if isinstance(v, dict) and str(v.get("id") or "").strip() == selected_id:
                    selected_variant = v
                    break
        if selected_variant is None:
            selected_variant = next((v for v in variants_payload if isinstance(v, dict)), None)

        target_variants: List[dict] = []
        if mode_norm == "selected":
            if selected_variant:
                target_variants = [selected_variant]
        elif mode_norm == "all":
            target_variants = [v for v in variants_payload if isinstance(v, dict)]
        else:
            def _basename(value: str) -> str:
                token = (value or "").split("?", 1)[0]
                token = token.rstrip("/").strip()
                if not token:
                    return ""
                return token.split("/")[-1]

            wanted = {"00_thumb_1.png", "00_thumb_2.png"}
            for v in variants_payload:
                if not isinstance(v, dict):
                    continue
                image_path = str(v.get("image_path") or "").strip()
                image_url = str(v.get("image_url") or "").strip()
                base = _basename(image_path) or _basename(image_url)
                if base in wanted:
                    target_variants.append(v)

        for raw_variant in target_variants:
            image_path = str(raw_variant.get("image_path") or "").strip()
            if not image_path:
                continue
            rel = Path(image_path.lstrip("/"))
            if rel.is_absolute() or any(part == ".." for part in rel.parts):
                continue
            if not rel.parts or rel.parts[0].strip().upper() != channel_code:
                continue

            candidate = (THUMBNAIL_ASSETS_DIR / rel).resolve()
            try:
                candidate.relative_to(THUMBNAIL_ASSETS_DIR.resolve())
            except (OSError, ValueError):
                continue
            if not candidate.is_file():
                continue

            if mode_norm == "two_up":
                safe_variant = candidate.stem
            else:
                variant_id = str(raw_variant.get("id") or "").strip() or "variant"
                safe_variant = re.sub(r"[^0-9A-Za-zぁ-んァ-ン一-龥ー_-]+", "_", variant_id).strip("_") or "variant"
            arcname = f"{video_number}/{safe_variant}{candidate.suffix.lower() or '.png'}"
            if arcname in used_names:
                arcname = f"{video_number}/{safe_variant}_{uuid.uuid4().hex[:6]}{candidate.suffix.lower() or '.png'}"
            used_names.add(arcname)
            files.append((arcname, candidate))

    if not files:
        raise HTTPException(status_code=404, detail="no local thumbnail assets found for download")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, path in files:
            zf.write(path, arcname=arcname)
    buffer.seek(0)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{channel_code}_thumbnails_{mode_norm}_{ts}.zip"
    headers = {"Content-Disposition": f'attachment; filename=\"{filename}\"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


@app.post(
    "/api/workspaces/thumbnails/{channel}/library/{asset_name}/describe",
    response_model=ThumbnailDescriptionResponse,
)
def describe_thumbnail_library_asset(channel: str, asset_name: str):
    raise HTTPException(
        status_code=400,
        detail="thumbnail library describe is disabled: LLM API is not used for thumbnails",
    )


@app.get("/thumbnails/assets/{channel}/{video}/{asset_path:path}")
def get_thumbnail_asset(channel: str, video: str, asset_path: str):
    channel_code = channel.strip().upper()
    if not channel_code or Path(channel_code).name != channel_code:
        raise HTTPException(status_code=404, detail="invalid channel")
    video_number = _coerce_video_from_dir(video)
    if not video_number:
        raise HTTPException(status_code=404, detail="invalid video")
    if not asset_path or asset_path.strip() == "":
        raise HTTPException(status_code=404, detail="invalid asset")
    rel_asset = Path(asset_path)
    if rel_asset.is_absolute():
        raise HTTPException(status_code=404, detail="invalid asset")
    if any(part == ".." for part in rel_asset.parts):
        raise HTTPException(status_code=404, detail="invalid asset")

    candidates: List[tuple[Path, Path]] = []

    asset_root = THUMBNAIL_ASSETS_DIR / channel_code / video_number
    candidates.append((asset_root, asset_root / rel_asset))
    channel_dir = find_channel_directory(channel_code)
    if channel_dir:
        channel_root = channel_dir / "thumbnails" / video_number
        candidates.append((channel_root, channel_root / rel_asset))

    for root, candidate in candidates:
        if not root.exists():
            continue
        try:
            resolved_root = root.resolve()
            resolved_candidate = candidate.resolve()
            resolved_candidate.relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        if not resolved_candidate.is_file():
            continue
        media_type = mimetypes.guess_type(resolved_candidate.name)[0] or "application/octet-stream"
        headers = {"Cache-Control": "no-store", "Pragma": "no-cache", "Expires": "0"}
        return FileResponse(
            resolved_candidate,
            media_type=media_type,
            filename=resolved_candidate.name,
            headers=headers,
            content_disposition_type="inline",
        )

    raise HTTPException(status_code=404, detail="thumbnail asset not found")


@app.get("/thumbnails/library/{channel}/{asset_path:path}")
def get_thumbnail_library_asset(channel: str, asset_path: str):
    channel_code = channel.strip().upper()
    if not channel_code or Path(channel_code).name != channel_code:
        raise HTTPException(status_code=404, detail="invalid channel")
    _, candidate = _resolve_library_asset_path(channel_code, asset_path)
    media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache", "Expires": "0"}
    return FileResponse(
        candidate,
        media_type=media_type,
        filename=candidate.name,
        headers=headers,
        content_disposition_type="inline",
    )


@app.get("/api/channels/{channel}/videos/{video}", response_model=VideoDetailResponse)
def get_video_detail(channel: str, video: str):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status_missing = False
    status = load_status_optional(channel_code, video_number)
    if status is None:
        status_missing = True
        status = _default_status_payload(channel_code, video_number)
    metadata = status.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    # CSV の最新情報を統合する（UI が常に最新の企画情報を参照できるようにする）
    planning_row = None
    for row in planning_store.get_rows(channel_code, force_refresh=True):
        if normalize_video_number(row.video_number or "") == video_number:
            planning_row = row
            break
    if planning_row:
        row_raw = planning_row.raw
        if row_raw.get("タイトル"):
            metadata["sheet_title"] = row_raw.get("タイトル")
        if row_raw.get("作成フラグ"):
            metadata["sheet_flag"] = row_raw.get("作成フラグ")
        planning_section = get_planning_section(metadata)
        update_planning_from_row(planning_section, row_raw)
    # リテイクフラグはデフォルトで True（人が確定させたら false にする運用）
    redo_script = metadata.get("redo_script")
    if redo_script is None:
        redo_script = True
    redo_audio = metadata.get("redo_audio")
    if redo_audio is None:
        redo_audio = True
    redo_note = metadata.get("redo_note")

    stages_raw = status.get("stages", {}) or {}
    stages_meta, a_text_ok, audio_exists, srt_exists = _derive_effective_stages(
        channel_code=channel_code,
        video_number=video_number,
        stages=stages_raw if isinstance(stages_raw, dict) else {},
        metadata=metadata,
    )
    stages = (
        {key: _stage_status_value(value) for key, value in stages_meta.items() if key}
        if isinstance(stages_meta, dict)
        else {}
    )
    stage_details: Optional[Dict[str, Any]] = None
    if stages_meta:
        details_out: Dict[str, Any] = {}
        for stage_name, stage_payload in stages_meta.items():
            if not isinstance(stage_payload, dict):
                continue
            details = stage_payload.get("details")
            if not isinstance(details, dict) or not details:
                continue
            subset: Dict[str, Any] = {}
            for key in (
                "error",
                "error_codes",
                "issues",
                "fix_hints",
                "checked_path",
                "stats",
                "warnings",
                "warning_codes",
                "warning_issues",
                "manual_entrypoint",
            ):
                val = details.get(key)
                if val in (None, "", [], {}):
                    continue
                subset[key] = val
            if subset:
                details_out[stage_name] = subset
        stage_details = details_out or None
    raw_status_value = status.get("status", "unknown")
    published_locked = False
    if planning_row:
        progress_value = str(planning_row.raw.get("進捗") or planning_row.raw.get("progress") or "").strip()
        if progress_value:
            lower = progress_value.lower()
            if "投稿済み" in progress_value or "公開済み" in progress_value or lower in {"published", "posted"}:
                published_locked = True
    if not published_locked and bool(metadata.get("published_lock")):
        published_locked = True
    if published_locked:
        stages["audio_synthesis"] = "completed"
        stages["srt_generation"] = "completed"

    status_value = _derive_effective_video_status(
        raw_status=raw_status_value,
        stages=stages_meta,
        a_text_ok=a_text_ok,
        audio_exists=audio_exists,
        srt_exists=srt_exists,
        published_locked=published_locked,
    )
    if status_missing and not published_locked:
        status_value = "pending"
    script_validated = _stage_status_value(stages_meta.get("script_validation")) == "completed" or str(
        raw_status_value or ""
    ).strip().lower() == "script_validated"
    ready_for_audio = bool(metadata.get("ready_for_audio", False)) or script_validated
    base_dir = video_base_dir(channel_code, video_number)
    content_dir = base_dir / "content"

    assembled_path = content_dir / "assembled.md"
    assembled_human_path = content_dir / "assembled_human.md"
    script_audio_path = content_dir / "script_audio.txt"
    script_audio_human_path = content_dir / "script_audio_human.txt"

    warnings: List[str] = []
    if status_missing:
        warnings.append(f"status.json missing for {channel_code}-{video_number}")
    # TTS入力（Bテキスト）は final/a_text.txt を正とする（=実際に合成した入力スナップショット）。
    # audio_prep/script_sanitized.txt は編集用/中間の派生（存在する場合は更新候補）。
    final_dir = audio_final_dir(channel_code, video_number)
    final_tts_snapshot = final_dir / "a_text.txt"
    editable_tts_path = base_dir / "audio_prep" / "script_sanitized.txt"
    b_with_pauses = final_tts_snapshot
    tts_plain_path = final_tts_snapshot if final_tts_snapshot.exists() else editable_tts_path
    if not tts_plain_path.exists():
        warnings.append(f"TTS input missing for {channel_code}-{video_number} (a_text.txt / script_sanitized.txt)")
    tts_tagged_path = base_dir / "audio_prep" / "script_sanitized_with_pauses.txt"
    srt_path = resolve_srt_path(status, base_dir)
    audio_path = resolve_audio_path(status, base_dir)

    audio_duration = get_audio_duration_seconds(audio_path) if audio_path else None
    audio_updated_at = None
    if audio_path:
        try:
            audio_updated_at = datetime.fromtimestamp(audio_path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
        except OSError:
            audio_updated_at = None
    audio_quality_status = None
    audio_quality_summary = None
    audio_quality_report = None
    quality_meta = metadata.get("audio", {}).get("quality")
    if isinstance(quality_meta, dict):
        audio_quality_status = quality_meta.get("status") or quality_meta.get("label")
        audio_quality_summary = quality_meta.get("summary") or quality_meta.get("note")
        report_path = quality_meta.get("report") or quality_meta.get("log")
        if report_path:
            audio_quality_report = safe_relative_path(Path(report_path)) or report_path
    elif isinstance(quality_meta, str):
        audio_quality_status = quality_meta

    audio_metadata = normalize_audio_metadata(metadata.get("audio"))
    pause_map = None
    if isinstance(audio_metadata, dict):
        candidate = audio_metadata.get("pause_map")
        if isinstance(candidate, list):
            pause_map = candidate

    plain_tts = resolve_text_file(tts_plain_path) or ""

    tagged_path = tts_tagged_path
    tagged_tts = resolve_text_file(tagged_path) if tagged_path.exists() else None
    tts_source_path = tts_plain_path if tts_plain_path.exists() else None

    script_audio_content = resolve_text_file(script_audio_path) or plain_tts
    script_audio_human_content = resolve_text_file(script_audio_human_path)

    silence_plan: Optional[Sequence[float]] = None
    if isinstance(audio_metadata, dict):
        synthesis_meta = audio_metadata.get("synthesis")
        if isinstance(synthesis_meta, dict):
            plan_candidate = synthesis_meta.get("silence_plan")
            if isinstance(plan_candidate, list):
                silence_plan = plan_candidate

    if tagged_tts is None and plain_tts:
        tagged_tts = _compose_tagged_tts(plain_tts, silence_plan, pause_map)

    # A/B は人間編集版だけを見せる（初期値は最終B/Aから埋め、無ければ空）
    assembled_content = resolve_text_file(assembled_human_path) or resolve_text_file(assembled_path) or ""
    human_b_content = plain_tts

    youtube_description = _build_youtube_description(
        channel_code, video_number, metadata, metadata.get("sheet_title") or metadata.get("title")
    )

    if not audio_path:
        warnings.append(f"audio missing for {channel_code}-{video_number}")
    if not srt_path:
        warnings.append(f"srt missing for {channel_code}-{video_number}")

    # Alignment guard: surface planning/title drift explicitly in Episode Studio.
    alignment_status: Optional[str] = None
    alignment_reason: Optional[str] = None
    try:
        planning_raw = planning_row.raw if planning_row else None
        script_path = base_dir / "content" / "assembled_human.md"
        if not script_path.exists():
            script_path = base_dir / "content" / "assembled.md"

        status_value_align = "未計測"
        reasons: List[str] = []

        if not script_path.exists():
            status_value_align = "台本なし"
        elif not isinstance(planning_raw, dict):
            status_value_align = "未計測"
            reasons.append("planning行が見つかりません")
        else:
            planning_hash = planning_hash_from_row(planning_raw)
            catches = {c for c in iter_thumbnail_catches_from_row(planning_raw)}

            align_meta = metadata.get("alignment") if isinstance(metadata, dict) else None
            stored_planning_hash = None
            stored_script_hash = None
            if isinstance(align_meta, dict):
                stored_planning_hash = align_meta.get("planning_hash")
                stored_script_hash = align_meta.get("script_hash")

            if len(catches) > 1:
                status_value_align = "NG"
                reasons.append("サムネプロンプト先頭行が不一致")
            elif isinstance(stored_planning_hash, str) and isinstance(stored_script_hash, str):
                script_hash = sha1_file_bytes(script_path)
                mismatch: List[str] = []
                if planning_hash != stored_planning_hash:
                    mismatch.append("タイトル/サムネ")
                if script_hash != stored_script_hash:
                    mismatch.append("台本")
                if mismatch:
                    status_value_align = "NG"
                    reasons.append("変更検出: " + " & ".join(mismatch))
                else:
                    status_value_align = "OK"
            else:
                status_value_align = "未計測"

            if isinstance(align_meta, dict) and bool(align_meta.get("suspect")):
                if status_value_align == "OK":
                    status_value_align = "要確認"
                suspect_reason = str(align_meta.get("suspect_reason") or "").strip()
                if suspect_reason:
                    reasons.append(suspect_reason)

        alignment_status = status_value_align
        alignment_reason = " / ".join(reasons) if reasons else None
    except Exception:
        alignment_status = None
        alignment_reason = None

    return VideoDetailResponse(
        channel=channel_code,
        video=video_number,
        script_id=status.get("script_id") or (planning_row.script_id if planning_row else None),
        title=metadata.get("sheet_title") or metadata.get("title"),
        status=status_value,
        ready_for_audio=bool(ready_for_audio),
        stages=stages,
        stage_details=stage_details,
        redo_script=bool(redo_script),
        redo_audio=bool(redo_audio),
        redo_note=redo_note,
        alignment_status=alignment_status,
        alignment_reason=alignment_reason,
        # A：人間編集版のみ（なければ空）。パスは human があればそれ、無ければ assembled を返す
        assembled_path=safe_relative_path(assembled_human_path) if assembled_human_path.exists() else (safe_relative_path(assembled_path) if assembled_path.exists() else None),
        assembled_content=assembled_content,
        assembled_human_path=None,
        assembled_human_content=None,
        # B：最終TTS入力（final/a_text.txt を優先。なければ audio_prep/script_sanitized.txt）。
        tts_path=safe_relative_path(tts_source_path) if tts_source_path else None,
        tts_content=human_b_content,
        tts_plain_content=human_b_content,
        tts_tagged_path=safe_relative_path(tagged_path) if tagged_path.exists() else None,
        tts_tagged_content=tagged_tts,
        # script_audio は ui 互換のため保持（未生成時は tts_plain を返す）
        script_audio_path=safe_relative_path(tts_plain_path) if tts_plain_path.exists() else None,
        script_audio_content=script_audio_content,
        script_audio_human_path=safe_relative_path(script_audio_human_path) if script_audio_human_path.exists() else None,
        script_audio_human_content=script_audio_human_content,
        srt_path=safe_relative_path(srt_path) if srt_path else None,
        srt_content=resolve_text_file(srt_path) if srt_path else None,
        audio_path=safe_relative_path(audio_path) if audio_path else None,
        audio_url=f"/api/channels/{channel_code}/videos/{video_number}/audio" if audio_path else None,
        audio_duration_seconds=audio_duration,
        audio_updated_at=audio_updated_at,
        audio_quality_status=audio_quality_status,
        audio_quality_summary=audio_quality_summary,
        audio_quality_report=audio_quality_report,
        audio_metadata=audio_metadata,
        tts_pause_map=pause_map,
        audio_reviewed=bool(metadata.get("audio_reviewed", False)),
        updated_at=status.get("updated_at"),
        completed_at=status.get("completed_at"),
        ui_session_token=status.get("ui_session_token"),
        planning=build_planning_payload(metadata),
        youtube_description=youtube_description,
        warnings=warnings,
        artifacts=_summarize_video_detail_artifacts(
            channel_code,
            video_number,
            base_dir=base_dir,
            content_dir=content_dir,
            audio_prep_dir=base_dir / "audio_prep",
            assembled_path=assembled_path,
            assembled_human_path=assembled_human_path,
            b_text_with_pauses_path=b_with_pauses,
            audio_path=audio_path,
            srt_path=srt_path,
        ),
    )


def _clear_redo_flags(channel: str, video: str, *, redo_script: Optional[bool] = None, redo_audio: Optional[bool] = None):
    """
    ベストエフォートでリテイクフラグを更新する（API内部利用）。音声成功時は redo_audio=False、台本保存時は redo_script=False などに利用。
    """
    try:
        channel_code = normalize_channel_code(channel)
        video_number = normalize_video_number(video)
        status = load_status(channel_code, video_number)
        meta = status.setdefault("metadata", {})
        if redo_script is not None:
            meta["redo_script"] = bool(redo_script)
        if redo_audio is not None:
            meta["redo_audio"] = bool(redo_audio)
        status["metadata"] = meta
        status["updated_at"] = current_timestamp()
        save_status(channel_code, video_number, status)
    except Exception:
        # ベストエフォートなので握りつぶす
        pass


def _resolve_script_pipeline_input_path(channel: str, video: str) -> Path:
    """
    旧式の解決（後方互換）。呼び出し元は _resolve_final_tts_input_path を優先すること。
    """
    base = DATA_ROOT / channel / video
    candidates = [
        base / "audio_prep" / "script_sanitized.txt",
        base / "content" / "assembled.md",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise HTTPException(status_code=404, detail=f"script_pipeline input not found: {channel}-{video}")


def _resolve_final_tts_input_path(channel: str, video: str) -> Path:
    """
    標準の音声生成で必ず参照する「AテキストSoT」を解決する。
    優先度:
    1) content/assembled_human.md
    2) content/assembled.md

    重要: 旧運用の `script_sanitized.txt` / `script_audio_human.txt` 等へ暗黙フォールバックしない。
    （入力不足は 404 で止め、事故を防ぐ）
    見つからない場合は 404 を返す。
    """
    base = DATA_ROOT / channel / video
    candidates = [
        base / "content" / "assembled_human.md",
        base / "content" / "assembled.md",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise HTTPException(status_code=404, detail=f"final tts input not found: {channel}-{video}")


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


@app.get("/api/channels/{channel}/videos/{video}/a-text", response_class=PlainTextResponse)
def api_get_a_text(channel: str, video: str):
    """
    Aテキスト（表示用原稿）を返す。優先順位:
    content/assembled_human.md -> content/assembled.md
    """
    channel_code = normalize_channel_code(channel)
    video_no = normalize_video_number(video)
    path = _resolve_a_text_display_path(channel_code, video_no)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="A-text not found")
    return text


@app.post("/api/audio-tts/run-from-script")
def api_audio_tts_run_from_script(
    channel: str = Body(..., embed=True),
    video: str = Body(..., embed=True),
    engine_override: Optional[str] = Body(None),
    reading_source: Optional[str] = Body(None),
):
    channel_code = normalize_channel_code(channel)
    video_no = normalize_video_number(video)
    input_path = _resolve_final_tts_input_path(channel_code, video_no)
    payload = TtsRequest(
        channel=channel_code,
        video=video_no,
        input_path=str(input_path),
        engine_override=engine_override,
        reading_source=reading_source,
    )
    return _run_audio_tts(payload)


# === audio_tts integration (simple CLI bridge) ===
class TtsRequest(BaseModel):
    channel: str
    video: str
    input_path: str
    engine_override: Optional[str] = Field(None, description="voicevox|voicepeak|elevenlabs を強制する場合")
    reading_source: Optional[str] = Field(None, description="voicepeak用読み取得ソース")
    voicepeak_narrator: Optional[str] = None
    voicepeak_speed: Optional[int] = None
    voicepeak_pitch: Optional[int] = None
    voicepeak_emotion: Optional[str] = None


def _run_audio_tts(req: TtsRequest) -> Dict[str, Any]:
    repo_root = REPO_ROOT  # Use constant defined at top
    pkg_root = audio_pkg_root()
    script = pkg_root / "scripts" / "run_tts.py"
    if not script.exists():
        raise HTTPException(status_code=500, detail="run_tts.py not found")
    input_path = Path(req.input_path)
    if not input_path.is_absolute():
        input_path = (repo_root / input_path).resolve()
    if not input_path.exists():
        raise HTTPException(status_code=400, detail=f"input_path not found: {input_path}")
    env = os.environ.copy()
    # Ensure imports resolve in subprocess even when started outside repo root.
    pythonpath_prefix = f"{repo_root}{os.pathsep}{repo_root / 'packages'}"
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{pythonpath_prefix}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else pythonpath_prefix
    )
    cmd = [
        sys.executable,
        str(script),
        "--channel",
        req.channel,
        "--video",
        req.video,
        "--input",
        str(input_path),
    ]

    # NOTE: Do NOT force --out-wav/--log here.
    # run_tts must write intermediates under workspaces/scripts/**/audio_prep/
    # (including audio_prep/script_sanitized.txt) and then sync to workspaces/audio/final/.
    final_dir = audio_final_dir(req.channel, req.video)
    final_wav_path = final_dir / f"{req.channel}-{req.video}.wav"
    final_srt_path = final_dir / f"{req.channel}-{req.video}.srt"
    final_log_path = final_dir / "log.json"
    if req.engine_override:
        cmd.extend(["--engine-override", req.engine_override])
    if req.reading_source:
        cmd.extend(["--reading-source", req.reading_source])
    if req.voicepeak_narrator:
        cmd.extend(["--voicepeak-narrator", req.voicepeak_narrator])
    if req.voicepeak_speed is not None:
        cmd.extend(["--voicepeak-speed", str(req.voicepeak_speed)])
    if req.voicepeak_pitch is not None:
        cmd.extend(["--voicepeak-pitch", str(req.voicepeak_pitch)])
    if req.voicepeak_emotion:
        cmd.extend(["--voicepeak-emotion", req.voicepeak_emotion])
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, env=env, check=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"audio_tts failed: {e.stderr or e.stdout or e}")
    stdout = completed.stdout.strip()
    if not final_wav_path.exists():
        raise HTTPException(status_code=500, detail=f"audio_tts did not create wav: {stdout}")
    wav_file_path = str(final_wav_path.resolve())
    srt_file_path = str(final_srt_path.resolve()) if final_srt_path.exists() else None
    log_file_path = str(final_log_path.resolve()) if final_log_path.exists() else None

    audio_url = f"/api/channels/{req.channel}/videos/{req.video}/audio"
    srt_url = f"/api/channels/{req.channel}/videos/{req.video}/srt" if final_srt_path.exists() else None
    log_url = f"/api/channels/{req.channel}/videos/{req.video}/log" if final_log_path.exists() else None
    engine = req.engine_override
    if not engine:
        m = re.search(r"Engine=([a-zA-Z0-9_]+)", stdout)
        if m:
            engine = m.group(1).lower()
    llm_meta = None

    # リテイク(音声)は成功時に自動で解除（ベストエフォート）
    _clear_redo_flags(req.channel, req.video, redo_audio=False)
    # 音声が成功しても台本リテイクが残っている場合は明示的に残す（redo_scriptは触らない）

    # 生成後の残骸（巨大chunk等）は削除して散らかりを抑える（最終成果物は削除しない）
    cleanup: Dict[str, Any] = {}
    try:
        prep_dir = DATA_ROOT / req.channel / req.video / "audio_prep"
        chunks_dir = prep_dir / "chunks"
        if chunks_dir.is_dir():
            shutil.rmtree(chunks_dir)
            cleanup["audio_prep_chunks_removed"] = str(chunks_dir)
    except Exception as exc:  # pragma: no cover - best effort
        cleanup["audio_prep_chunks_error"] = str(exc)

    try:
        prep_dir = DATA_ROOT / req.channel / req.video / "audio_prep"
        prep_wav = prep_dir / f"{req.channel}-{req.video}.wav"
        prep_srt = prep_dir / f"{req.channel}-{req.video}.srt"
        if prep_wav.exists() and final_wav_path.exists():
            prep_wav.unlink()
            cleanup["audio_prep_wav_removed"] = str(prep_wav)
        if prep_srt.exists() and final_srt_path.exists():
            prep_srt.unlink()
            cleanup["audio_prep_srt_removed"] = str(prep_srt)
    except Exception as exc:  # pragma: no cover - best effort
        cleanup["audio_prep_binaries_error"] = str(exc)

    keep_chunks_env = (os.getenv("YTM_TTS_KEEP_CHUNKS") or "").strip().lower()
    keep_chunks = keep_chunks_env in {"1", "true", "yes", "y", "on"}
    if not keep_chunks:
        try:
            final_chunks_dir = final_dir / "chunks"
            if final_chunks_dir.is_dir():
                shutil.rmtree(final_chunks_dir)
                cleanup["final_chunks_removed"] = str(final_chunks_dir)
        except Exception as exc:  # pragma: no cover - best effort
            cleanup["final_chunks_error"] = str(exc)

    return {
        "engine": engine,
        # Backward-compatible keys (front-end expects URL-ish strings, not absolute file paths)
        "wav_path": audio_url,
        "srt_path": srt_url,
        "log": log_url,
        "stdout": stdout,
        "final_wav": audio_url,
        "final_srt": srt_url,
        "llm_meta": llm_meta,
        # Debug-only extras (not part of response_model)
        "wav_file_path": wav_file_path,
        "srt_file_path": srt_file_path,
        "log_file_path": log_file_path,
        "cleanup": cleanup or None,
    }


@app.post("/api/audio-tts/run")
def api_audio_tts_run(payload: TtsRequest):
    channel_code = normalize_channel_code(payload.channel)
    video_no = normalize_video_number(payload.video)
    resolved = _resolve_final_tts_input_path(channel_code, video_no)

    provided = Path(payload.input_path)
    repo_root = REPO_ROOT  # Use constant defined at top
    if not provided.is_absolute():
        provided = (repo_root / provided).resolve()

    if provided.resolve() != resolved.resolve():
        raise HTTPException(
            status_code=400,
            detail=f"input_path must be final script: {resolved} (provided: {provided})",
        )

    fixed = payload.copy()
    fixed.channel = channel_code
    fixed.video = video_no
    fixed.input_path = str(resolved)
    return _run_audio_tts(fixed)


class TtsBatchItem(BaseModel):
    channel: str
    video: str
    input_path: str
    engine_override: Optional[str] = None
    reading_source: Optional[str] = None
    voicepeak_narrator: Optional[str] = None
    voicepeak_speed: Optional[int] = None
    voicepeak_pitch: Optional[int] = None
    voicepeak_emotion: Optional[str] = None


class TtsBatchResponse(BaseModel):
    results: List[Dict[str, Any]]
    success_count: int
    failure_count: int


@app.post("/api/audio-tts/run-batch", response_model=TtsBatchResponse)
def api_audio_tts_run_batch(payload: List[TtsBatchItem]):
    results: List[Dict[str, Any]] = []
    success = 0
    failure = 0
    for item in payload:
        try:
            channel_code = normalize_channel_code(item.channel)
            video_no = normalize_video_number(item.video)
            resolved = _resolve_final_tts_input_path(channel_code, video_no)
            provided = Path(item.input_path)
            repo_root = REPO_ROOT  # Use constant defined at top
            if not provided.is_absolute():
                provided = (repo_root / provided).resolve()
            if provided.resolve() != resolved.resolve():
                raise HTTPException(
                    status_code=400,
                    detail=f"input_path must be final script: {resolved} (provided: {provided})",
                )
            res = _run_audio_tts(
                TtsRequest(
                    channel=channel_code,
                    video=video_no,
                    input_path=str(resolved),
                    engine_override=item.engine_override,
                    reading_source=item.reading_source,
                    voicepeak_narrator=item.voicepeak_narrator,
                    voicepeak_speed=item.voicepeak_speed,
                    voicepeak_pitch=item.voicepeak_pitch,
                    voicepeak_emotion=item.voicepeak_emotion,
                )
            )
            results.append({"channel": item.channel, "video": item.video, "ok": True, **res})
            success += 1
        except HTTPException as exc:
            results.append(
                {
                    "channel": item.channel,
                    "video": item.video,
                    "ok": False,
                    "error": exc.detail,
                    "status_code": exc.status_code,
                }
            )
            failure += 1
        except Exception as exc:  # pragma: no cover - best effort
            results.append(
                {
                    "channel": item.channel,
                    "video": item.video,
                    "ok": False,
                    "error": str(exc),
                }
            )
            failure += 1
    return TtsBatchResponse(results=results, success_count=success, failure_count=failure)


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


# Planning CSV expose (viewer-friendly CSV rows)
@app.get("/api/planning/channels/{channel_code}")
def api_planning_channel(channel_code: str):
    channel_code = normalize_channel_code(channel_code)
    csv_path = CHANNEL_PLANNING_DIR / f"{channel_code}.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="planning csv not found")
    try:
        import csv
        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        # merge redo flags from status.json (default True when missing)
        for row in rows:
            video_num = row.get("動画番号") or row.get("video") or row.get("Video") or ""
            norm_video = normalize_planning_video_number(video_num)
            if not norm_video:
                continue
            meta: Dict[str, Any] = {}
            try:
                st = load_status(channel_code, norm_video)
                meta = st.get("metadata", {}) if isinstance(st, dict) else {}
                redo_script = meta.get("redo_script") if isinstance(meta, dict) else None
                redo_audio = meta.get("redo_audio") if isinstance(meta, dict) else None
                if redo_script is None:
                    redo_script = True
                if redo_audio is None:
                    redo_audio = True
                row["redo_script"] = bool(redo_script)
                row["redo_audio"] = bool(redo_audio)
                if isinstance(meta, dict) and meta.get("redo_note"):
                    row["redo_note"] = meta.get("redo_note")
            except Exception:
                row["redo_script"] = True
                row["redo_audio"] = True

            # 投稿済みロック: ここから先は触らない指標（redoも強制OFF）
            progress_value = str(row.get("進捗") or row.get("progress") or "").strip()
            published_locked = ("投稿済み" in progress_value) or ("公開済み" in progress_value)
            if not published_locked:
                published_locked = is_episode_published_locked(channel_code, norm_video)
            row["published_lock"] = bool(published_locked)
            if published_locked:
                row["redo_script"] = False
                row["redo_audio"] = False
            # thumbnail autofill (if not explicitly provided)
            has_thumb = False
            for key in ["thumbnail_url", "サムネURL", "サムネ画像URL", "サムネ画像"]:
                if isinstance(row.get(key), str) and row.get(key).strip():
                    has_thumb = True
                    if key != "thumbnail_url":
                        row["thumbnail_url"] = row.get(key).strip()
                    break
            if not has_thumb:
                override_url = meta.get("thumbnail_url_override")
                override_path = meta.get("thumbnail_path_override")
                if isinstance(override_url, str) and override_url.strip():
                    row["thumbnail_url"] = override_url.strip()
                    if isinstance(override_path, str) and override_path.strip():
                        row["thumbnail_path"] = override_path.strip()
                    has_thumb = True
            if not has_thumb:
                try:
                    title = row.get("タイトル") or row.get("title") or None
                    thumbs = thumbnails_lookup_tools.find_thumbnails(channel_code, norm_video, title, limit=1)
                    if thumbs:
                        row["thumbnail_url"] = thumbs[0]["url"]
                        row["thumbnail_path"] = thumbs[0]["path"]
                except Exception:
                    pass

            # === Alignment guard (title/thumbnail/script) ===
            # Goal: prevent "どれが完成版？" confusion by making misalignment explicit.
            try:
                base_dir = DATA_ROOT / channel_code / norm_video
                script_path = base_dir / "content" / "assembled_human.md"
                if not script_path.exists():
                    script_path = base_dir / "content" / "assembled.md"

                planning_hash = planning_hash_from_row(row)
                catches = {c for c in iter_thumbnail_catches_from_row(row)}

                align_meta = meta.get("alignment") if isinstance(meta, dict) else None
                stored_planning_hash = None
                stored_script_hash = None
                if isinstance(align_meta, dict):
                    stored_planning_hash = align_meta.get("planning_hash")
                    stored_script_hash = align_meta.get("script_hash")

                status_value = "未計測"
                reasons: list[str] = []

                if not script_path.exists():
                    status_value = "台本なし"
                elif len(catches) > 1:
                    status_value = "NG"
                    reasons.append("サムネプロンプト先頭行が不一致")
                elif isinstance(stored_planning_hash, str) and isinstance(stored_script_hash, str):
                    script_hash = sha1_file_bytes(script_path)
                    mismatch: list[str] = []
                    if planning_hash != stored_planning_hash:
                        mismatch.append("タイトル/サムネ")
                    if script_hash != stored_script_hash:
                        mismatch.append("台本")
                    if mismatch:
                        status_value = "NG"
                        reasons.append("変更検出: " + " & ".join(mismatch))
                    else:
                        status_value = "OK"
                else:
                    status_value = "未計測"

                if isinstance(align_meta, dict) and bool(align_meta.get("suspect")):
                    if status_value == "OK":
                        status_value = "要確認"
                    suspect_reason = str(align_meta.get("suspect_reason") or "").strip()
                    if suspect_reason:
                        reasons.append(suspect_reason)

                row["整合"] = status_value
                if reasons:
                    row["整合理由"] = " / ".join(reasons)
            except Exception:
                # never break progress listing
                row["整合"] = row.get("整合") or "未計測"
        return {"channel": channel_code, "rows": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to read csv: {e}")
