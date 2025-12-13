"""FastAPI backend for the React UI."""

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
import base64
import unicodedata
import requests
from PIL import Image, ImageStat
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Literal, Sequence
from collections import deque
from enum import Enum
import sqlite3
import threading

import logging

from fastapi.staticfiles import StaticFiles
# audio_tts_v2 routing helpers
from audio_tts_v2.tts.routing import (
    load_routing_config,
    resolve_eleven_model,
    resolve_eleven_voice,
    resolve_voicevox_speaker_id,
)
from audio_tts_v2.tts.reading_dict import (
    ReadingEntry,
    is_banned_surface,
    load_channel_reading_dict,
    merge_channel_readings,
    save_channel_reading_dict,
    normalize_reading_kana,
    is_safe_reading,
)
from audio_tts_v2.tts.mecab_tokenizer import tokenize_with_mecab
from audio_tts_v2.tts.auditor import calc_kana_mismatch_score
FILE_PATH = Path(__file__).resolve()
BACKEND_ROOT = FILE_PATH.parent
UI_ROOT = BACKEND_ROOT.parent
PROJECT_ROOT = UI_ROOT.parent
REPO_ROOT = PROJECT_ROOT.parent
for p in (BACKEND_ROOT, UI_ROOT, PROJECT_ROOT, REPO_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# Ensure .env is loaded even when uvicorn is started outside the repo root.
def _load_root_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        # Prefer python-dotenv if available
        try:
            from dotenv import load_dotenv  # type: ignore

            load_dotenv(dotenv_path=env_path, override=False)
            return
        except Exception:
            pass

        # Fallback: minimal parser
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    except Exception:
        # Fail-soft: backend should still start
        pass

_load_root_env()

# Provide ui/backend import alias when launched via uvicorn from arbitrary cwd
import importlib
import types
if "ui" not in sys.modules:
    ui_pkg = types.ModuleType("ui")
    ui_pkg.__path__ = [str(UI_ROOT)]
    sys.modules["ui"] = ui_pkg
if "ui.backend" not in sys.modules:
    try:
        backend_mod = importlib.import_module("backend")
        sys.modules["ui.backend"] = backend_mod
    except Exception:
        pass

try:
    import portalocker  # type: ignore
except ImportError:  # pragma: no cover - fallback shim for test environments
    import threading
    from typing import Optional, TextIO

    class _LockException(Exception):
        pass

    class _Timeout(_LockException):
        pass

    class _Exceptions:
        Timeout = _Timeout
        LockException = _LockException

    _LOCKS: Dict[str, threading.Lock] = {}

    class _StubLock:
        def __init__(
            self,
            filename: str,
            mode: str = "r",
            timeout: Optional[float] = None,
            encoding: Optional[str] = None,
        ) -> None:
            self._filename = str(Path(filename))
            self._mode = mode
            self._encoding = encoding
            self._timeout = timeout
            self._file: Optional[TextIO] = None
            _LOCKS.setdefault(self._filename, threading.Lock())
            self._lock = _LOCKS[self._filename]

        def __enter__(self) -> TextIO:
            if self._timeout is None:
                acquired = self._lock.acquire()
            else:
                acquired = self._lock.acquire(timeout=self._timeout)
            if not acquired:
                raise _Timeout(f"Failed to acquire lock on {self._filename}")
            self._file = open(self._filename, self._mode, encoding=self._encoding)
            return self._file

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            try:
                if self._file:
                    self._file.close()
            finally:
                self._lock.release()
            return False

    def _lock_factory(
        filename: str,
        mode: str = "r",
        timeout: Optional[float] = None,
        encoding: Optional[str] = None,
    ) -> _StubLock:
        return _StubLock(filename, mode=mode, timeout=timeout, encoding=encoding)

    class _PortalockerStub:
        exceptions = _Exceptions()
        Lock = staticmethod(_lock_factory)
        Timeout = _Timeout
        LockException = _LockException

    portalocker = _PortalockerStub()  # type: ignore
else:  # pragma: no cover - ensure compatibility across portalocker versions
    timeout_cls = getattr(portalocker, "Timeout", RuntimeError)
    lock_exc_cls = getattr(portalocker, "LockException", RuntimeError)
    exceptions_obj = getattr(portalocker, "exceptions", None)
    if exceptions_obj is None:
        class _CompatExceptions:
            Timeout = timeout_cls
            LockException = lock_exc_cls

        portalocker.exceptions = _CompatExceptions()  # type: ignore[attr-defined]
    else:
        try:
            setattr(exceptions_obj, "Timeout", timeout_cls)
            setattr(exceptions_obj, "LockException", lock_exc_cls)
        except Exception:  # pragma: no cover - robustness
            pass
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form, Body, BackgroundTasks
from backend.routers import jobs
from fastapi import APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from types import SimpleNamespace
import urllib.parse
import subprocess
import os

from audio import pause_tags, wav_tools
from audio.script_loader import iterate_sections
from core.tools import workflow_precheck as workflow_precheck_tools
from core.tools.content_processor import ContentProcessor
from core.tools.audio_manager import AudioManager
from core.tools.channel_profile import load_channel_profile
from core.tools.prompt_utils import auto_placeholder_values
# 移行先: script_pipeline/tools 配下の簡易実装を利用
from script_pipeline.tools import planning_requirements, planning_store
from script_pipeline.tools import openrouter_models as openrouter_model_utils
from app.youtube_client import YouTubeDataClient, YouTubeDataAPIError
from backend.video_production import video_router
from backend.routers import swap
from backend.routers import params
from factory_common.paths import (
    audio_final_dir,
    audio_pkg_root,
    logs_root as ssot_logs_root,
    planning_root as ssot_planning_root,
    repo_root as ssot_repo_root,
    script_data_root as ssot_script_data_root,
    script_pkg_root,
    thumbnails_root as ssot_thumbnails_root,
    video_pkg_root,
)

_llm_usage_import_error: Exception | None = None
try:
    from ui.backend.routers import llm_usage
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

LOGGER_NAME = "ui.backend"
logger = logging.getLogger(LOGGER_NAME)
APPS_ROOT = Path(__file__).resolve().parents[2]
# NOTE: PROJECT_ROOT is treated as repo-root throughout this file.
PROJECT_ROOT = ssot_repo_root()
# ensure repository root on sys.path so that `ui.*` imports resolve when launched via uvicorn
repo_root = PROJECT_ROOT
backend_root = Path(__file__).resolve().parent
for p in (repo_root, APPS_ROOT, backend_root):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
# 旧 commentary_01_srtfile_v2 から script_pipeline へ移行済み
COMMENTARY01_ROOT = script_pkg_root()
COMMENTARY02_ROOT = video_pkg_root()
DATA_ROOT = ssot_script_data_root()
EXPORTS_DIR = COMMENTARY01_ROOT / "exports"
PLANNING_CSV_PATH = None  # legacy master unused; channel CSVs are SoT
CHANNEL_PLANNING_DIR = ssot_planning_root() / "channels"
PROMPTS_ROOT = PROJECT_ROOT / "prompts"
COMMENTARY_PROMPTS_ROOT = COMMENTARY01_ROOT / "prompts"
SPREADSHEET_EXPORT_DIR = EXPORTS_DIR / "spreadsheets"
THUMBNAIL_PROJECTS_CANDIDATES = [
    ssot_thumbnails_root() / "projects.json",
]
THUMBNAIL_ASSETS_DIR = ssot_thumbnails_root() / "assets"
THUMBNAIL_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
THUMBNAIL_PROJECTS_LOCK = threading.Lock()
UI_SETTINGS_PATH = PROJECT_ROOT / "configs" / "ui_settings.json"
LLM_REGISTRY_PATH = PROJECT_ROOT / "configs" / "llm_registry.json"
PROMPT_TEMPLATES_ROOT = COMMENTARY_PROMPTS_ROOT / "templates"
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
LLM_MODEL_SCORES_PATH = PROJECT_ROOT / "ssot" / "HISTORY_llm_model_scores.json"
KB_PATH = audio_pkg_root() / "data" / "global_knowledge_base.json"
UI_LOG_DIR = LOGS_ROOT / "ui"
TASK_LOG_DIR = UI_LOG_DIR / "batch_workflow"
TASK_DB_PATH = UI_LOG_DIR / "ui_tasks.db"
TASK_TABLE = "batch_tasks"
QUEUE_TABLE = "batch_queue"
QUEUE_CONFIG_DIR = TASK_LOG_DIR / "queue_configs"
QUEUE_PROGRESS_DIR = TASK_LOG_DIR / "queue_progress"
from core.llm import LLMFactory, ModelPhase, ModelConfig, LLMProvider

OPENAI_CAPTION_DEFAULT_MODEL = os.getenv("OPENAI_DEFAULT_CAPTION_MODEL", "gpt-5-chat")
DEFAULT_CAPTION_PROVIDER = os.getenv("THUMBNAIL_CAPTION_PROVIDER", "openai")
DEFAULT_OPENAI_CAPTION_MODEL = os.getenv("OPENAI_DEFAULT_CAPTION_MODEL", OPENAI_CAPTION_DEFAULT_MODEL)
NATURAL_COMMAND_DEFAULT_MODEL = "deepseek/deepseek-chat-v3.1:free"

SETTINGS_LOCK = threading.Lock()
DEFAULT_UI_SETTINGS: Dict[str, Any] = {
    "llm": {
        "caption_provider": DEFAULT_CAPTION_PROVIDER,
        "openai_api_key": None,
        "openai_caption_model": DEFAULT_OPENAI_CAPTION_MODEL,
        "openrouter_api_key": None,
        "openrouter_caption_model": "qwen/qwen3-14b:free",
        # Phase models are now managed by LLMRegistry, but kept here for UI compatibility
        "phase_models": {},
    }
}
UI_SETTINGS: Dict[str, Any] = {}


def _prompt_spec(
    prompt_id: str,
    label: str,
    primary_path: Path,
    *,
    description: Optional[str] = None,
    sync_paths: Optional[list[Path]] = None,
    channel_code: Optional[str] = None,
    channel_info_path: Optional[Path] = None,
) -> Dict[str, Any]:
    return {
        "id": prompt_id,
        "label": label,
        "description": description,
        "primary_path": primary_path,
        "sync_paths": sync_paths or [],
        "channel_code": channel_code,
        "channel_info_path": channel_info_path,
    }


def _discover_template_prompt_specs() -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    if not PROMPT_TEMPLATES_ROOT.exists():
        return specs
    root_templates = PROMPTS_ROOT / "templates"
    for path in sorted(PROMPT_TEMPLATES_ROOT.glob("*.txt")):
        stem = path.stem
        specs.append(
            _prompt_spec(
                prompt_id=f"template_{stem}",
                label=f"テンプレート {stem}",
                description=f"{stem} 用の台本テンプレート",
                primary_path=path,
                sync_paths=[root_templates / path.name],
            )
        )
    return specs


def _parse_channel_code(dir_name: str) -> Optional[str]:
    name = dir_name.strip()
    if not name.upper().startswith("CH"):
        return None
    return name.split("-")[0].upper()


def _discover_channel_prompt_specs() -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    if not CHANNELS_DIR.exists():
        return specs
    for entry in sorted(CHANNELS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        channel_code = _parse_channel_code(entry.name)
        if not channel_code:
            continue
        primary = entry / "script_prompt.txt"
        if not primary.exists():
            continue
        sync_root = PROMPTS_ROOT / "channels" / channel_code
        specs.append(
            _prompt_spec(
                prompt_id=f"channel_{channel_code.lower()}_script_prompt",
                label=f"{channel_code} script_prompt",
                description="チャンネル固有の台本テンプレート（channel_info.template_path と同期）",
                primary_path=primary,
                sync_paths=[sync_root / "script_prompt.txt"],
                channel_code=channel_code,
                channel_info_path=entry / "channel_info.json",
            )
        )
    return specs


def _load_prompt_documents() -> Dict[str, Dict[str, Any]]:
    base_specs: List[Dict[str, Any]] = [
        _prompt_spec(
            prompt_id="youtube_description_prompt",
            label="YouTube説明文プロンプト",
            description="SRTから投稿用説明文を生成するテンプレート",
            primary_path=PROMPTS_ROOT / "youtube_description_prompt.txt",
            sync_paths=[COMMENTARY_PROMPTS_ROOT / "youtube_description_prompt.txt"],
        ),
        _prompt_spec(
            prompt_id="phase2_audio_prompt",
            label="台本→音声フェーズプロンプト",
            primary_path=COMMENTARY_PROMPTS_ROOT / "phase2_audio_prompt.txt",
            sync_paths=[PROMPTS_ROOT / "phase2_audio_prompt.txt"],
        ),
        _prompt_spec(
            prompt_id="llm_polish_template",
            label="台本ポリッシュプロンプト",
            primary_path=COMMENTARY_PROMPTS_ROOT / "llm_polish_template.txt",
            sync_paths=[PROMPTS_ROOT / "llm_polish_template.txt"],
        ),
        _prompt_spec(
            prompt_id="orchestrator_prompt",
            label="オーケストレータプロンプト",
            primary_path=COMMENTARY_PROMPTS_ROOT / "orchestrator_prompt.txt",
            sync_paths=[PROMPTS_ROOT / "orchestrator_prompt.txt"],
        ),
        _prompt_spec(
            prompt_id="chapter_enhancement_prompt",
            label="章エンハンスプロンプト",
            primary_path=COMMENTARY_PROMPTS_ROOT / "chapter_enhancement_prompt.txt",
            sync_paths=[PROMPTS_ROOT / "chapter_enhancement_prompt.txt"],
        ),
        _prompt_spec(
            prompt_id="init_prompt",
            label="初期化プロンプト (init)",
            primary_path=COMMENTARY_PROMPTS_ROOT / "init.txt",
            sync_paths=[PROMPTS_ROOT / "init.txt"],
        ),
    ]
    template_specs = _discover_template_prompt_specs()
    channel_specs = _discover_channel_prompt_specs()
    merged: Dict[str, Dict[str, Any]] = {}
    for spec in [*base_specs, *template_specs, *channel_specs]:
        merged[spec["id"]] = spec
    return merged


def _load_llm_model_scores() -> List[LlmModelInfo]:
    if not LLM_MODEL_SCORES_PATH.exists():
        return []
    try:
        raw_entries = json.loads(LLM_MODEL_SCORES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - configuration error
        logger.error("Failed to parse %s: %s", LLM_MODEL_SCORES_PATH, exc)
        raise HTTPException(status_code=500, detail="LLMモデル情報の読み込みに失敗しました。")
    models: List[LlmModelInfo] = []
    for entry in raw_entries:
        try:
            models.append(LlmModelInfo(**entry))
        except Exception as exc:  # pragma: no cover - validation issue
            logger.warning("Skipping invalid LLM model entry: %s", exc)
    return models


def _normalize_llm_settings(raw: Optional[dict]) -> dict:
    """Normalize settings using LLMRegistry defaults."""
    llm = copy.deepcopy(DEFAULT_UI_SETTINGS["llm"])
    if not isinstance(raw, dict):
        return llm
    
    # Copy basic settings
    for key in ["caption_provider", "openai_api_key", "openai_caption_model", "openrouter_api_key", "openrouter_caption_model"]:
        if raw.get(key):
            llm[key] = raw.get(key)

    # Merge phase models from registry and raw input
    registry = LLMFactory.get_registry()
    merged_phase_models: Dict[str, Dict[str, object]] = {}
    
    # 1. Start with registry defaults
    for phase, config in registry.phases.items():
        merged_phase_models[phase.value] = {
            "label": config.label or phase.value,
            "provider": config.provider.value,
            "model": config.model,
        }

    # 2. Override with incoming raw settings
    incoming_phases = raw.get("phase_models") or {}
    for phase_id, incoming in incoming_phases.items():
        if not isinstance(incoming, dict):
            continue
        current = merged_phase_models.get(phase_id, {})
        merged_phase_models[phase_id] = {
            "label": incoming.get("label") or current.get("label") or phase_id,
            "provider": incoming.get("provider") or current.get("provider") or "openrouter",
            "model": incoming.get("model") or current.get("model"),
        }

    llm["phase_models"] = merged_phase_models
    return llm


def _resolve_phase_choice(
    llm: Dict[str, Any],
    phase_id: str,
    *,
    default_provider: str,
    default_model: str,
    allowed_providers: Optional[set[str]] = None,
) -> tuple[str, str]:
    # Try to use LLMFactory logic if possible, but for UI resolution we might need raw dict access
    phase_models = llm.get("phase_models") or {}
    entry = phase_models.get(phase_id) or {}
    
    # If entry is empty, try to get from registry
    if not entry:
        try:
            phase_enum = ModelPhase(phase_id)
            config = LLMFactory.get_registry().get_config(phase_enum)
            entry = {
                "provider": config.provider.value,
                "model": config.model
            }
        except ValueError:
            pass

    provider = (entry.get("provider") or default_provider).lower()
    model = entry.get("model") or default_model
    if allowed_providers and provider not in allowed_providers:
        provider = default_provider
    _validate_provider_endpoint(provider)
    return provider, model


def _validate_provider_endpoint(provider: str) -> None:
    """
    Fail-fast to avoid sending OpenRouter payloads to Azure or vice versa.
    """
    if provider == "openrouter":
        base = os.getenv("OPENAI_BASE_URL", "").lower()
        if "cognitiveservices.azure.com" in base:
            raise HTTPException(
                status_code=400,
                detail=(
                    "provider=openrouter ですが OPENAI_BASE_URL が Azure を指しています。"
                    " OPENAI_BASE_URL=https://openrouter.ai/api/v1 にしてください。"
                ),
            )
        key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_TOKEN")
        if not key:
            raise HTTPException(status_code=400, detail="provider=openrouter には OPENROUTER_API_KEY が必要です。")
    elif provider == "openai":
        if not _get_effective_openai_key():
            raise HTTPException(status_code=400, detail="provider=openai には OpenAI/Azure APIキーが必要です。")
    elif provider == "gemini":
        if not os.getenv("GEMINI_API_KEY"):
            raise HTTPException(status_code=400, detail="provider=gemini には GEMINI_API_KEY が必要です。")


def _load_ui_settings_from_disk() -> None:
    global UI_SETTINGS
    with SETTINGS_LOCK:
        settings = copy.deepcopy(DEFAULT_UI_SETTINGS)
        if UI_SETTINGS_PATH.exists():
            try:
                loaded = json.loads(UI_SETTINGS_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    settings["llm"] = _normalize_llm_settings(loaded.get("llm"))
            except Exception as exc:  # pragma: no cover - corrupted settings
                logger.warning("Failed to read %s: %s", UI_SETTINGS_PATH, exc)
        # registry があれば phase_models を上書き
        if LLM_REGISTRY_PATH.exists():
            try:
                registry = json.loads(LLM_REGISTRY_PATH.read_text(encoding="utf-8"))
                if isinstance(registry, dict):
                    settings["llm"]["phase_models"] = registry
            except Exception as exc:  # pragma: no cover
                logger.warning("Failed to read llm registry %s: %s", LLM_REGISTRY_PATH, exc)
        UI_SETTINGS = settings


def _write_ui_settings(settings: Dict[str, Any]) -> None:
    UI_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS_LOCK:
        UI_SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        UI_SETTINGS.update(copy.deepcopy(settings))
        # phase_models を registry にも書き出す
        phase_models = settings.get("llm", {}).get("phase_models") or {}
        try:
            LLM_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
            LLM_REGISTRY_PATH.write_text(json.dumps(phase_models, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to write llm registry %s: %s", LLM_REGISTRY_PATH, exc)


def _get_ui_settings() -> Dict[str, Any]:
    with SETTINGS_LOCK:
        return copy.deepcopy(UI_SETTINGS)


_load_ui_settings_from_disk()


def _ensure_openrouter_api_key() -> str:
    getter = globals().get("_get_ui_settings")
    if callable(getter):
        settings = getter()
    else:  # pragma: no cover - defensive fallback for reload edge cases
        logger.error("_get_ui_settings is unavailable during OpenRouter key resolution; using defaults.")
        settings = copy.deepcopy(DEFAULT_UI_SETTINGS)
    value = settings.get("llm", {}).get("openrouter_api_key")
    if value:
        return value
    value = os.getenv("OPENROUTER_API_KEY") or _load_env_value("OPENROUTER_API_KEY")
    if value:
        return value
    if os.getenv("YTM_ALLOW_OPENROUTER_MISSING") == "1":
        logger.warning(
            "OPENROUTER_API_KEY is not configured, but YTM_ALLOW_OPENROUTER_MISSING=1 so continuing in degraded mode."
        )
        return ""
    raise RuntimeError(
        "OPENROUTER_API_KEY が設定されていません。`.env` を更新し `python scripts/check_env.py --keys OPENROUTER_API_KEY` "
        "を通過させてから UI を起動してください。"
    )

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
    planning_store.refresh(force=False)
    if planning_store.list_channels():
        return
    detail = "channels CSV がまだ生成されていません。ssot_sync を実行してください。"
    raise HTTPException(status_code=503, detail=detail)


def _persona_doc_path(channel_code: str) -> Path:
    return PROJECT_ROOT / "progress" / "personas" / f"{channel_code}_PERSONA.md"


def _planning_template_path(channel_code: str) -> Path:
    return PROJECT_ROOT / "progress" / "templates" / f"{channel_code}_planning_template.csv"


def _relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _collect_required_columns(channel_code: str) -> List[str]:
    specs = planning_requirements.get_channel_requirement_specs(channel_code)
    columns: List[str] = []
    for spec in specs:
        spec_columns = spec.get("required_columns") or []
        for column in spec_columns:
            if column not in columns:
                columns.append(column)
    return columns


def _preview_csv_content(content: str) -> Tuple[List[str], List[str]]:
    stream = io.StringIO(content)
    reader = csv.reader(stream)
    try:
        headers = next(reader)
    except StopIteration as exc:
        raise HTTPException(status_code=400, detail="CSVにヘッダー行がありません。") from exc
    sample = next(reader, [])
    return headers, sample


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


def _read_channels_csv_rows() -> Tuple[List[str], List[Dict[str, str]]]:
    raise HTTPException(status_code=404, detail="channels CSV は使用しません（channels CSV が SoT）。")
    with PLANNING_CSV_PATH.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


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
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
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


def _write_queue_config(channel_code: str, config: BatchWorkflowConfig) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = QUEUE_CONFIG_DIR / f"{timestamp}_{channel_code}_{uuid.uuid4().hex}.json"
    write_json(path, config.model_dump())
    return path


def _load_queue_config(path: str) -> BatchWorkflowConfig:
    data = load_json(Path(path))
    return BatchWorkflowConfig(**data)

UI_LOG_DIR.mkdir(parents=True, exist_ok=True)
TASK_LOG_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_PROGRESS_DIR.mkdir(parents=True, exist_ok=True)


def init_task_db() -> None:
    TASK_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(TASK_DB_PATH) as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TASK_TABLE} (
                task_id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                videos TEXT NOT NULL,
                status TEXT NOT NULL,
                log_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {QUEUE_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                videos TEXT NOT NULL,
                status TEXT NOT NULL,
                config_path TEXT NOT NULL,
                task_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


init_task_db()


class BatchTaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class QueueEntryStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


TASK_REGISTRY: Dict[str, Dict[str, Any]] = {}


def _serialize_videos(videos: List[str]) -> str:
    return ",".join(videos)


def _deserialize_videos(payload: str) -> List[str]:
    if not payload:
        return []
    return [item for item in payload.split(",") if item]


def _task_db_execute(query: str, params: Tuple[Any, ...]) -> None:
    with sqlite3.connect(TASK_DB_PATH) as conn:
        conn.execute(query, params)


def _queue_db_query(query: str, params: Tuple[Any, ...] = ()) -> List[Tuple[Any, ...]]:
    with sqlite3.connect(TASK_DB_PATH) as conn:
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
    return rows


def _queue_db_execute(query: str, params: Tuple[Any, ...]) -> None:
    with sqlite3.connect(TASK_DB_PATH) as conn:
        conn.execute(query, params)


def _queue_progress_path(entry_id: int) -> Path:
    return QUEUE_PROGRESS_DIR / f"{entry_id}.json"


def _init_queue_progress(entry_id: int, total_count: int) -> None:
    payload = {
        "processed": 0,
        "total": total_count,
        "current_video": None,
        "status": QueueEntryStatus.queued.value,
        "updated_at": current_timestamp(),
    }
    write_json(_queue_progress_path(entry_id), payload)


_UNSET = object()


def _update_queue_progress(
    entry_id: int,
    *,
    processed: Optional[int] = None,
    total: Optional[int] = None,
    current_video: Any = _UNSET,
    status: Optional[str] = None,
    issues: Optional[Dict[str, str]] = None,
) -> None:
    path = _queue_progress_path(entry_id)
    if not path.exists():
        return
    try:
        data = load_json(path)
    except HTTPException:
        data = {}
    changed = False
    if processed is not None:
        data["processed"] = processed
        changed = True
    if total is not None:
        data["total"] = total
        changed = True
    if current_video is not _UNSET:
        data["current_video"] = current_video
        changed = True
    if status is not None:
        data["status"] = status
        changed = True
    if issues is not None:
        data["issues"] = issues
        changed = True
    if changed:
        data["updated_at"] = current_timestamp()
        write_json(path, data)


def _load_queue_progress(entry_id: int) -> Dict[str, Any]:
    path = _queue_progress_path(entry_id)
    if not path.exists():
        return {}
    try:
        return load_json(path)
    except HTTPException:
        return {}


def _remove_queue_progress(entry_id: int) -> None:
    path = _queue_progress_path(entry_id)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _validate_video_prerequisites(channel_code: str, video_number: str) -> Tuple[bool, Optional[str]]:
    # バリデーションはスクリプト側に委譲する（新規作成時など、SSOTにまだ行がない場合もあるため）
    return True, None


def persist_task_record(task_id: str, channel: str, videos: List[str], status: BatchTaskStatus, log_path: Path) -> None:
    _task_db_execute(
        f"REPLACE INTO {TASK_TABLE} (task_id, channel, videos, status, log_path, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            task_id,
            channel,
            _serialize_videos(videos),
            status.value,
            str(log_path),
            current_timestamp(),
        ),
    )


def update_task_status(task_id: str, status: BatchTaskStatus) -> None:
    _task_db_execute(
        f"UPDATE {TASK_TABLE} SET status=? WHERE task_id=?",
        (status.value, task_id),
    )
    if task_id in TASK_REGISTRY:
        TASK_REGISTRY[task_id]["status"] = status.value


def load_task_record(task_id: str) -> Optional[Dict[str, Any]]:
    with sqlite3.connect(TASK_DB_PATH) as conn:
        row = conn.execute(
            f"SELECT task_id, channel, videos, status, log_path, created_at FROM {TASK_TABLE} WHERE task_id=?",
            (task_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "task_id": row[0],
        "channel": row[1],
        "videos": _deserialize_videos(row[2]),
        "status": row[3],
        "log_path": row[4],
        "created_at": row[5],
    }


def _channel_is_busy(channel_code: str) -> bool:
    busy_states = (BatchTaskStatus.pending.value, BatchTaskStatus.running.value)
    for record in TASK_REGISTRY.values():
        if record.get("channel_code") == channel_code and record.get("status") in busy_states:
            return True
    placeholders = ",".join("?" for _ in busy_states)
    rows = _queue_db_query(
        f"SELECT 1 FROM {TASK_TABLE} WHERE channel=? AND status IN ({placeholders}) LIMIT 1",
        (channel_code, *busy_states),
    )
    if rows:
        return True
    rows = _queue_db_query(
        f"SELECT 1 FROM {QUEUE_TABLE} WHERE channel=? AND status=? LIMIT 1",
        (channel_code, QueueEntryStatus.running.value),
    )
    return bool(rows)


def _hydrate_queue_row(row: Tuple[Any, ...]) -> Dict[str, Any]:
    progress = _load_queue_progress(int(row[0]))
    return {
        "id": row[0],
        "channel_code": row[1],
        "video_numbers": _deserialize_videos(row[2]),
        "status": row[3],
        "config_path": row[4],
        "task_id": row[5],
        "created_at": row[6],
        "updated_at": row[7],
        "processed_count": progress.get("processed"),
        "total_count": progress.get("total"),
        "current_video": progress.get("current_video"),
        "issues": progress.get("issues"),
    }


def insert_queue_entry(channel_code: str, video_numbers: List[str], config: BatchWorkflowConfig) -> int:
    config_path = _write_queue_config(channel_code, config)
    now = current_timestamp()
    total_count = len(video_numbers)
    with sqlite3.connect(TASK_DB_PATH) as conn:
        cursor = conn.execute(
            f"INSERT INTO {QUEUE_TABLE} (channel, videos, status, config_path, task_id, created_at, updated_at) VALUES (?, ?, ?, ?, NULL, ?, ?)",
            (
                channel_code,
                _serialize_videos(video_numbers),
                QueueEntryStatus.queued.value,
                str(config_path),
                now,
                now,
            ),
        )
        entry_id = cursor.lastrowid
    _init_queue_progress(entry_id, total_count)
    return entry_id


def get_queue_entry(entry_id: int) -> Optional[Dict[str, Any]]:
    rows = _queue_db_query(
        f"SELECT id, channel, videos, status, config_path, task_id, created_at, updated_at FROM {QUEUE_TABLE} WHERE id=?",
        (entry_id,),
    )
    if not rows:
        return None
    return _hydrate_queue_row(rows[0])


def list_queue_entries(channel_code: Optional[str] = None) -> List[Dict[str, Any]]:
    if channel_code:
        rows = _queue_db_query(
            f"SELECT id, channel, videos, status, config_path, task_id, created_at, updated_at FROM {QUEUE_TABLE} WHERE channel=? ORDER BY created_at",
            (channel_code,),
        )
    else:
        rows = _queue_db_query(
            f"SELECT id, channel, videos, status, config_path, task_id, created_at, updated_at FROM {QUEUE_TABLE} ORDER BY created_at",
            (),
        )
    return [_hydrate_queue_row(row) for row in rows]


def get_next_queue_entry(channel_code: str) -> Optional[Dict[str, Any]]:
    rows = _queue_db_query(
        f"SELECT id, channel, videos, status, config_path, task_id, created_at, updated_at FROM {QUEUE_TABLE} WHERE channel=? AND status=? ORDER BY created_at LIMIT 1",
        (channel_code, QueueEntryStatus.queued.value),
    )
    if not rows:
        return None
    return _hydrate_queue_row(rows[0])


def update_queue_entry_status(entry_id: int, status: QueueEntryStatus, *, task_id: Optional[str] = None) -> None:
    now = current_timestamp()
    if task_id is not None:
        _queue_db_execute(
            f"UPDATE {QUEUE_TABLE} SET status=?, updated_at=?, task_id=? WHERE id=?",
            (status.value, now, task_id, entry_id),
        )
    else:
        _queue_db_execute(
            f"UPDATE {QUEUE_TABLE} SET status=?, updated_at=? WHERE id=?",
            (status.value, now, entry_id),
        )


def delete_queue_entry(entry_id: int) -> None:
    _queue_db_execute(f"DELETE FROM {QUEUE_TABLE} WHERE id=?", (entry_id,))
    _remove_queue_progress(entry_id)


def _build_queue_response(entry: Dict[str, Any]):
    return BatchQueueEntryResponse(
        id=entry["id"],
        channel_code=entry["channel_code"],
        video_numbers=entry["video_numbers"],
        status=entry["status"],
        task_id=entry.get("task_id"),
        created_at=entry["created_at"],
        updated_at=entry["updated_at"],
        processed_count=entry.get("processed_count"),
        total_count=entry.get("total_count"),
        current_video=entry.get("current_video"),
    )


async def _maybe_start_queue(channel_code: str) -> Optional[str]:
    if _channel_is_busy(channel_code):
        return None
    entry = get_next_queue_entry(channel_code)
    if not entry:
        return None
    errors: Dict[str, str] = {}
    valid_videos: List[str] = []
    for video in entry["video_numbers"]:
        ok, reason = _validate_video_prerequisites(channel_code, video)
        if ok:
            valid_videos.append(video)
        else:
            errors[video] = reason or "未知の理由"
    if errors:
        logger.warning(
            "Queue entry %s skipped due to missing prerequisites: %s",
            entry["id"],
            errors,
        )
        update_queue_entry_status(entry["id"], QueueEntryStatus.failed)
        _update_queue_progress(
            entry["id"],
            status=QueueEntryStatus.failed.value,
            current_video=None,
            issues=errors,
        )
        return None
    config = _load_queue_config(entry["config_path"])
    task_id = _launch_batch_task(channel_code, valid_videos, config, queue_entry_id=entry["id"])
    update_queue_entry_status(entry["id"], QueueEntryStatus.running, task_id=task_id)
    _update_queue_progress(entry["id"], status=QueueEntryStatus.running.value, processed=entry.get("processed_count") or 0)
    return task_id


async def _handle_task_completion(task_id: str, channel_code: str, status: BatchTaskStatus) -> None:
    record = TASK_REGISTRY.get(task_id)
    queue_id = record.get("queue_id") if record else None
    if queue_id:
        if status == BatchTaskStatus.succeeded:
            update_queue_entry_status(queue_id, QueueEntryStatus.succeeded)
            _update_queue_progress(queue_id, status=QueueEntryStatus.succeeded.value, current_video=None)
        elif status == BatchTaskStatus.failed:
            update_queue_entry_status(queue_id, QueueEntryStatus.failed)
            _update_queue_progress(queue_id, status=QueueEntryStatus.failed.value, current_video=None)
    await _maybe_start_queue(channel_code)


ENV_FILE_CANDIDATES = [
    PROJECT_ROOT / ".env",
    PROJECT_ROOT / "ui" / ".env",
    PROJECT_ROOT / "00_research" / ".env",
    PROJECT_ROOT.parent / ".env",
    PROJECT_ROOT.parent / "00_research" / ".env",
]


def _load_env_value(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value:
        return value
    for env_path in ENV_FILE_CANDIDATES:
        if not env_path or not env_path.exists():
            continue
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith(f"{name}="):
                    _, raw_value = line.split("=", 1)
                    os.environ[name] = raw_value
                    logger.info("Loaded %s from %s", name, env_path)
                    return raw_value
        except Exception as exc:  # pragma: no cover - best-effort parsing
            logger.warning("Failed to parse %s for %s: %s", env_path, name, exc)
    return None


PROGRESS_STATUS_PATH = DATA_ROOT / "_progress" / "processing_status.json"
CHANNELS_DIR = COMMENTARY01_ROOT / "channels"
CHANNEL_INFO_PATH = CHANNELS_DIR / "channels_info.json"
AUDIO_CHANNELS_DIR = COMMENTARY01_ROOT / "audio" / "channels"
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


def _get_effective_openrouter_key() -> Optional[str]:
    settings = _get_ui_settings()
    key = settings.get("llm", {}).get("openrouter_api_key")
    if key:
        return key
    return OPENROUTER_API_KEY or None


def _get_effective_openai_key() -> Optional[str]:
    settings = _get_ui_settings()
    key = settings.get("llm", {}).get("openai_api_key")
    if key:
        return key
    return os.getenv("OPENAI_API_KEY") or _load_env_value("OPENAI_API_KEY")

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
THUMBNAIL_CAPTION_DEFAULT_MODEL = "qwen/qwen2.5-vl-32b-instruct:free"
THUMBNAIL_CAPTION_FALLBACK_MODELS = [
    "qwen/qwen3-vl-8b-instruct:free",
    "qwen/qwen3-vl-8b-instruct",
    "qwen/qwen3-vl-30b-a3b-instruct",
    "qwen/qwen2.5-vl-32b-instruct",
    "qwen/qwen-2.5-vl-7b-instruct",
]

if not os.getenv("YOUTUBE_API_KEY"):
    _load_env_value("YOUTUBE_API_KEY")

OPENROUTER_API_KEY = _ensure_openrouter_api_key()

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

CHANNEL_INFO_LOCK = threading.Lock()
YOUTUBE_CLIENT = YouTubeDataClient.from_env()
if YOUTUBE_CLIENT is None:
    logger.warning("YOUTUBE_API_KEY が設定されていないため、YouTube Data API からのサムネイル取得をスキップします。ローカル案のプレビューにフォールバックします。")
CHANNEL_INFO: Dict[str, dict] = {}
CHANNEL_INFO_MTIME: float = 0.0


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
        audio_default_voice_key=voice_payload.get("default_voice_key"),
        audio_section_voice_rules=audio_rules if isinstance(audio_rules, dict) else {},
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


def _relative_prompt_path(path: Path) -> str:
    rel = safe_relative_path(path)
    return rel if rel else str(path)


def _prompt_checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _get_prompt_spec(prompt_id: str) -> Dict[str, Any]:
    spec = _load_prompt_documents().get(prompt_id)
    if not spec:
        raise HTTPException(status_code=404, detail="指定したプロンプトは登録されていません。")
    return spec


def _describe_prompt_sync_target(path: Path) -> PromptSyncTargetResponse:
    rel_path = _relative_prompt_path(path)
    if not path.exists():
        return PromptSyncTargetResponse(path=rel_path, exists=False)
    try:
        stat = path.stat()
        content = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem failure
        logger.exception("Failed to read prompt sync target %s: %s", path, exc)
        raise HTTPException(status_code=500, detail=f"{rel_path} の読み込みに失敗しました。") from exc
    return PromptSyncTargetResponse(
        path=rel_path,
        exists=True,
        checksum=_prompt_checksum(content),
        updated_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def _build_prompt_document_payload(spec: Dict[str, Any], *, include_content: bool) -> Dict[str, Any]:
    primary_path: Path = spec["primary_path"]
    rel_path = _relative_prompt_path(primary_path)
    if not primary_path.exists():
        raise HTTPException(status_code=404, detail=f"{spec.get('label', spec['id'])} が見つかりません: {rel_path}")
    try:
        stat = primary_path.stat()
        content = primary_path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem failure
        logger.exception("Failed to read prompt %s: %s", primary_path, exc)
        raise HTTPException(status_code=500, detail=f"{rel_path} の読み込みに失敗しました。") from exc
    checksum = _prompt_checksum(content)
    sync_targets = []
    for sync_path in spec.get("sync_paths", []) or []:
        if sync_path == primary_path:
            continue
        sync_targets.append(_describe_prompt_sync_target(sync_path))
    payload: Dict[str, Any] = {
        "id": spec["id"],
        "label": spec.get("label", spec["id"]),
        "description": spec.get("description"),
        "relative_path": rel_path,
        "size_bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "checksum": checksum,
        "sync_targets": sync_targets,
    }
    if include_content:
        payload["content"] = content
    return payload


def _persist_prompt_document(spec: Dict[str, Any], *, new_content: str, previous_content: str) -> None:
    unique_paths: List[Path] = []
    for path in [spec["primary_path"], * (spec.get("sync_paths", []) or [])]:
        if path not in unique_paths:
            unique_paths.append(path)
    updated_paths: List[Path] = []
    try:
        for path in unique_paths:
            write_text_with_lock(path, new_content)
            updated_paths.append(path)
        # channel_info.json へも反映（script_prompt フィールド）
        channel_info_path = spec.get("channel_info_path")
        if channel_info_path:
            if channel_info_path.exists():
                try:
                    info_payload = load_json(channel_info_path)
                except HTTPException:
                    logger.warning("Failed to load channel_info.json for prompt sync: %s", channel_info_path)
                else:
                    if info_payload.get("script_prompt") != new_content.strip():
                        info_payload["script_prompt"] = new_content.strip()
                        write_json(channel_info_path, info_payload)
            else:
                logger.warning("channel_info.json not found for prompt sync: %s", channel_info_path)
    except HTTPException:
        for path in updated_paths:
            try:
                write_text_with_lock(path, previous_content)
            except HTTPException:
                logger.exception("Failed to roll back prompt file %s", path)
        raise
    except Exception as exc:  # pragma: no cover - unexpected failure
        logger.exception("Unexpected error while updating prompt: %s", exc)
        for path in updated_paths:
            try:
                write_text_with_lock(path, previous_content)
            except HTTPException:
                logger.exception("Failed to roll back prompt file %s", path)
        raise HTTPException(status_code=500, detail="プロンプトの更新に失敗しました。もう一度お試しください。") from exc


def _register_task(
    task_id: str,
    channel_code: str,
    video_numbers: List[str],
    log_path: Path,
    status: BatchTaskStatus,
    *,
    config_path: Optional[Path] = None,
    queue_id: Optional[int] = None,
) -> None:
    TASK_REGISTRY[task_id] = {
        "channel": channel_code,
        "channel_code": channel_code,
        "video_numbers": video_numbers,
        "log_path": str(log_path),
        "config_path": str(config_path) if config_path else None,
        "status": status.value,
        "created_at": current_timestamp(),
        "queue_id": queue_id,
    }
    persist_task_record(task_id, channel_code, video_numbers, status, log_path)


def _launch_batch_task(
    channel_code: str,
    video_numbers: List[str],
    config: BatchWorkflowConfig,
    *,
    queue_entry_id: Optional[int] = None,
) -> str:
    task_id = uuid.uuid4().hex
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    log_path = TASK_LOG_DIR / f"{timestamp}_{channel_code}_{task_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch()

    # llm_model の解決 (Noneならデフォルト設定を適用)
    if config.llm_model is None:
        current_settings = _get_ui_settings()
        _, resolved_model = _resolve_phase_choice(
            current_settings.get("llm", {}),
            "script_rewrite",
            default_provider="openrouter",
            default_model="qwen/qwen3-14b:free"
        )
        config.llm_model = resolved_model

    config_path = log_path.with_suffix(".config.json")
    config_payload = config.model_dump()
    config_payload.update(
        {
            "task_id": task_id,
            "channel_code": channel_code,
            "video_numbers": video_numbers,
        }
    )
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(config_payload, handle, ensure_ascii=False, indent=2)
    _register_task(
        task_id,
        channel_code,
        video_numbers,
        log_path,
        BatchTaskStatus.pending,
        config_path=config_path,
        queue_id=queue_entry_id,
    )
    asyncio.create_task(run_batch_workflow_task(task_id, channel_code, video_numbers, config))
    return task_id


def _build_batch_command(
    channel_code: str,
    video_number: str,
    config: BatchWorkflowConfig,
    config_path: Optional[str] = None,
) -> List[str]:
    cmd = [
        sys.executable,
        str(COMMENTARY01_ROOT / "qwen" / "batch_workflow.py"),
        "--channel-code",
        channel_code,
        "--video-number",
        video_number,
        "--auto-confirm",
    ]
    # Note: config.loop_mode is handled by the backend runner (continue on error),
    # so we do NOT pass --loop to the CLI script to avoid autonomous processing.
    if config_path:
        cmd.extend(["--config-file", config_path])
    return cmd


async def run_batch_workflow_task(task_id: str, channel_code: str, video_numbers: List[str], config: BatchWorkflowConfig) -> None:
    record = TASK_REGISTRY[task_id]
    log_path = Path(record["log_path"])
    config_path = record.get("config_path")
    update_task_status(task_id, BatchTaskStatus.running)
    final_status = BatchTaskStatus.succeeded
    queue_id = record.get("queue_id")
    total_videos = len(video_numbers)
    has_failure = False
    
    if queue_id:
        _update_queue_progress(queue_id, total=total_videos, status=QueueEntryStatus.running.value, processed=0, current_video=None)
    
    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            for index, video in enumerate(video_numbers):
                if queue_id:
                    _update_queue_progress(queue_id, processed=index, current_video=f"{channel_code}-{video}")
                
                log_file.write(f"=== {datetime.now().isoformat()} / {channel_code}-{video} ===\n")
                log_file.flush()
                
                cmd = _build_batch_command(channel_code, video, config, config_path)
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(COMMENTARY01_ROOT),
                )
                assert process.stdout is not None
                async for raw_line in process.stdout:
                    line = raw_line.decode("utf-8", errors="ignore")
                    log_file.write(line)
                    log_file.flush()
                
                returncode = await process.wait()
                
                if returncode != 0:
                    log_file.write(f"\n❌ batch_workflow exit code {returncode}\n")
                    log_file.flush()
                    has_failure = True
                    
                    if config.loop_mode:
                        log_file.write(f"⚠️ ループモード有効: エラーを無視して次の動画へ進みます。\n\n")
                        log_file.flush()
                        # 失敗しても processed カウントは進める（処理済みとして扱う）
                        if queue_id:
                            _update_queue_progress(queue_id, processed=index + 1, current_video=None)
                        continue
                    else:
                        update_task_status(task_id, BatchTaskStatus.failed)
                        if queue_id:
                            _update_queue_progress(queue_id, current_video=None, status=QueueEntryStatus.failed.value)
                        final_status = BatchTaskStatus.failed
                        return

                if queue_id:
                    _update_queue_progress(queue_id, processed=index + 1, current_video=None)

        if has_failure:
            final_status = BatchTaskStatus.failed
        
        update_task_status(task_id, final_status)
        if queue_id:
            status_val = QueueEntryStatus.succeeded.value if not has_failure else QueueEntryStatus.failed.value
            _update_queue_progress(queue_id, processed=total_videos, current_video=None, status=status_val)

    except Exception as exc:  # pragma: no cover - defensive
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"\n❌ Exception: {exc}\n")
        logger.exception("Batch workflow task %s failed", task_id)
        update_task_status(task_id, BatchTaskStatus.failed)
        if queue_id:
            _update_queue_progress(queue_id, current_video=None, status=QueueEntryStatus.failed.value)
        final_status = BatchTaskStatus.failed
    finally:
        await _handle_task_completion(task_id, channel_code, final_status)


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


def list_video_dirs(channel_code: str) -> List[Path]:
    channel_dir = DATA_ROOT / channel_code
    if not channel_dir.exists():
        return []
    return sorted((p for p in channel_dir.iterdir() if p.is_dir() and p.name.isdigit()), key=lambda p: int(p.name))


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


def normalize_channel_code(channel: str) -> str:
    raw = channel.strip()
    if not raw or Path(raw).name != raw:
        raise HTTPException(status_code=400, detail="Invalid channel identifier")
    channel_code = raw.upper()
    channel_path = DATA_ROOT / channel_code
    if not channel_path.is_dir():
        raise HTTPException(status_code=404, detail=f"Channel {channel_code} not found")
    return channel_code


def normalize_video_number(video: str) -> str:
    raw = video.strip()
    if not raw or Path(raw).name != raw:
        raise HTTPException(status_code=400, detail="Invalid video identifier")
    if not raw.isdigit():
        raise HTTPException(status_code=400, detail="Video identifier must be numeric")
    return raw.zfill(3)


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
    """Locate WAV/SRT that were synced into commentary_02 input folders."""
    root = PROJECT_ROOT / "commentary_02_srt2images_timeline" / "input"
    if not root.exists():
        return None
    pattern = f"**/{channel_code}-{video_number}.{suffix}"
    for match in sorted(root.glob(pattern)):
        if match.is_file():
            return match.resolve()
    return None


def _infer_srt_duration_seconds(path: Path) -> Optional[float]:
    if not path.exists() or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    # Find last timestamp in HH:MM:SS,mmm
    import re

    matches = list(re.finditer(r"(\\d{2}):(\\d{2}):(\\d{2}),(\\d{3})", text))
    if not matches:
        return None
    hh, mm, ss, ms = matches[-1].groups()
    try:
        h = int(hh)
        m = int(mm)
        s = int(ss)
        ms_val = int(ms)
    except ValueError:
        return None
    return h * 3600 + m * 60 + s + ms_val / 1000.0


def _iter_video_dirs() -> Iterable[tuple[str, str, Path]]:
    if not DATA_ROOT.exists():
        return []
    for channel_dir in sorted(DATA_ROOT.iterdir()):
        if not channel_dir.is_dir():
            continue
        channel = channel_dir.name.upper()
        if not channel.startswith("CH"):
            continue
        for video_dir in sorted(channel_dir.iterdir()):
            if not video_dir.is_dir():
                continue
            video = video_dir.name
            yield channel, video, video_dir


def _load_audio_analysis(channel: str, video: str) -> AudioAnalysisResponse:
    channel_code = normalize_channel_code(channel)
    video_no = normalize_video_number(video)
    base_dir = video_base_dir(channel_code, video_no)
    audio_prep = base_dir / "audio_prep"
    warnings: List[str] = []

    b_path = audio_prep / "b_text_with_pauses.txt"
    pause_map_path = audio_prep / "pause_map.json"
    log_path = audio_prep / "log.json"
    engine_metadata_path = audio_prep / "engine_metadata.json"

    b_text = resolve_text_file(b_path) if b_path.exists() else None
    if not b_text:
        warnings.append("b_text_with_pauses.txt missing")

    raw_pause_map: Any = None
    pause_map: Optional[List[Dict[str, Any]]] = None
    if pause_map_path.exists():
        try:
            raw_pause_map = json.loads(pause_map_path.read_text(encoding="utf-8"))
        except Exception:
            warnings.append("pause_map.json parse failed")
    else:
        warnings.append("pause_map.json missing")

    if isinstance(raw_pause_map, dict):
        pauses = raw_pause_map.get("pauses")
        if isinstance(pauses, list):
            pause_map = []
            for idx, val in enumerate(pauses, start=1):
                try:
                    pause_val = float(val)
                except (TypeError, ValueError):
                    warnings.append(f"pause_map: invalid pause value at {idx}")
                    continue
                pause_map.append({"section": idx, "pause_sec": pause_val})
    elif isinstance(raw_pause_map, list):
        pause_map = []
        for idx, entry in enumerate(raw_pause_map, start=1):
            if isinstance(entry, dict):
                try:
                    pause_val = float(entry.get("pause_sec") or entry.get("pause") or entry.get("value") or 0.0)
                except (TypeError, ValueError):
                    warnings.append(f"pause_map: invalid pause value at {idx}")
                    continue
                try:
                    section_idx = int(
                        entry.get("section")
                        or entry.get("section_index")
                        or entry.get("index")
                        or entry.get("section_idx")
                        or idx
                    )
                except (TypeError, ValueError):
                    section_idx = idx
                pause_map.append({"section": section_idx, "pause_sec": pause_val})
            else:
                try:
                    pause_val = float(entry)
                except (TypeError, ValueError):
                    warnings.append(f"pause_map: invalid pause value at {idx}")
                    continue
                pause_map.append({"section": idx, "pause_sec": pause_val})
    elif raw_pause_map is not None:
        warnings.append("pause_map.json unexpected format (expected list or {pauses:[]})")

    if pause_map is not None and len(pause_map) == 0:
        warnings.append("pause_map.json has 0 entries")

    engine_meta: dict = {}
    for candidate in (engine_metadata_path, log_path):
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    if "engine_metadata" in data and isinstance(data["engine_metadata"], dict):
                        engine_meta = data["engine_metadata"]
                    else:
                        engine_meta = data
                    break
            except Exception:
                warnings.append(f"{candidate.name} parse failed")

    kana_diff = None
    raw_diff = engine_meta.get("voicevox_kana_diff") if isinstance(engine_meta, dict) else None
    if isinstance(raw_diff, dict):
        kana_diff = VoicevoxKanaDiff(
            engine_kana=str(raw_diff.get("engine_kana") or ""),
            llm_kana=str(raw_diff.get("llm_kana") or ""),
            diff=raw_diff.get("diff") or [],
        )

    return AudioAnalysisResponse(
        channel=channel_code,
        video=video_no,
        b_text_with_pauses=b_text,
        pause_map=pause_map,
        voicevox_kana=engine_meta.get("voicevox_kana") if isinstance(engine_meta, dict) else None,
        voicevox_kana_corrected=engine_meta.get("voicevox_kana_corrected") if isinstance(engine_meta, dict) else None,
        voicevox_kana_diff=kana_diff,
        voicevox_kana_llm_ref=engine_meta.get("voicevox_kana_llm_ref") if isinstance(engine_meta, dict) else None,
        voicevox_accent_phrases=engine_meta.get("voicevox_accent_phrases") if isinstance(engine_meta, dict) else None,
        warnings=warnings,
    )


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
    """Guard SoT by running progress_manager validate-status for the given row."""

    progress_manager = COMMENTARY01_ROOT / "core" / "tools" / "progress_manager.py"
    if not progress_manager.exists():
        logger.error("progress_manager.py is missing at %s", progress_manager)
        raise HTTPException(
            status_code=500,
            detail="SSOTガードの実行に必要な progress_manager が見つかりません。",
        )

    command = [
        sys.executable,
        str(progress_manager),
        "validate-status",
        "--channel-code",
        channel_code,
        "--video-number",
        video_number,
        "--context",
        "ssot-sync",
        "--json",
    ]
    logger.info(
        "Running progress_manager validate-status for %s-%s",
        channel_code,
        video_number,
    )
    env = os.environ.copy()
    env.setdefault("LOGURU_LEVEL", "INFO")
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    def _log_failure(payload: Optional[dict] = None) -> Path:
        SSOT_SYNC_LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = current_timestamp_compact()
        log_path = (
            SSOT_SYNC_LOG_DIR
            / f"ssot_sync_failure_{channel_code}_{video_number}_{timestamp}.json"
        )
        log_payload = {
            "channel_code": channel_code,
            "video_number": video_number,
            "command": command,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "payload": payload,
        }
        log_path.write_text(
            json.dumps(log_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return log_path

    if result.returncode != 0:
        log_path = _log_failure()
        logger.error(
            "progress_manager validate-status failed for %s-%s (log: %s)",
            channel_code,
            video_number,
            log_path,
        )
        raise HTTPException(
            status_code=502,
            detail="SSOT同期に失敗しました。ログを確認してから再試行してください。",
        )

    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        log_path = _log_failure()
        logger.error(
            "Could not parse validate-status output for %s-%s (log: %s)",
            channel_code,
            video_number,
            log_path,
        )
        raise HTTPException(
            status_code=502,
            detail="SSOT同期に失敗しました。ログを確認してから再試行してください。",
        )

    if not payload.get("success", False):
        log_path = _log_failure(payload)
        logger.error(
            "validate-status reported issues for %s-%s: %s",
            channel_code,
            video_number,
            payload.get("issues"),
        )
        raise HTTPException(
            status_code=502,
            detail="SSOT同期に失敗しました。ログを確認してから再試行してください。",
        )

    logger.info(
        "validate-status succeeded for %s-%s; SoT is in sync",
        channel_code,
        video_number,
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


def _stage_status_value(stage_entry: Optional[dict]) -> str:
    if not stage_entry:
        return "pending"
    status = stage_entry.get("status")
    if status in VALID_STAGE_STATUSES:
        return status
    return "unknown"


def _detect_artifact_path(channel_code: str, video_number: str, extension: str) -> Path:
    base = audio_final_dir(channel_code, video_number)
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


def _extract_script_summary(channel_code: str, video_number: str) -> Optional[str]:
    """Assembled台本の先頭パラグラフを短く切り出す。"""
    base_dir = video_base_dir(channel_code, video_number)
    candidates = [
        base_dir / "content" / "assembled_human.md",
        base_dir / "content" / "assembled.md",
    ]
    for path in candidates:
        try:
            if path.exists() and path.is_file():
                text = path.read_text(encoding="utf-8")
                if not text:
                    continue
                paragraph = text.split("\n\n")[0].strip()
                if not paragraph:
                    continue
                # 文の先頭2〜3文を抜粋
                sentences = [s for s in paragraph.replace("！", "。").replace("？", "。").split("。") if s.strip()]
                summary = "。".join(sentences[:3])
                return (summary + "。").strip() if summary else paragraph[:200]
        except Exception:
            continue
    return None


def _normalize_description_length(text: str, *, max_len: int = 900) -> str:
    if len(text) <= max_len:
        return text
    # できるだけ文単位で切る
    sentences = [s for s in text.split("。") if s.strip()]
    trimmed = ""
    for s in sentences:
        candidate = (trimmed + s + "。").strip()
        if len(candidate) > max_len:
            break
        trimmed = candidate
    if trimmed:
        return trimmed + "…"
    return text[: max_len - 1] + "…"


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

    lead = pget("description_lead")
    takeaways = pget("description_takeaways")
    audience = pget("target_audience")
    main_tag = pget("primary_pain_tag")
    sub_tag = pget("secondary_pain_tag")
    tags = []
    if main_tag:
        tags.append(f"#{main_tag}")
    if sub_tag:
        tags.append(f"#{sub_tag}")

    title_text = title or pget("sheet_title") or pget("title") or ""

    def bullet_list(text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        lines = [line.strip("・").strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return None
        return "・" + "\n・".join(lines)

    takeaways_block = bullet_list(takeaways)

    script_summary = _extract_script_summary(channel_code, video_number)
    summary_line = script_summary or lead

    if channel_code in {"CH01", "CH07", "CH11"}:
        opener = f"この動画では「{title_text}」を仏教の視点でやさしく解き明かします。"
        body = summary_line or "心が折れそうなときに使える“たった一言”をお届け。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：一呼吸おいて距離をとる / 優しさと境界線を両立する"
        hash_line = " ".join(tags) if tags else "#仏教 #心を整える #人間関係"
        parts = [opener, body, audience_line, take_line, hash_line]
        return _normalize_description_length("\n".join(filter(None, parts)))

    if channel_code in {"CH02", "CH10"}:
        opener = f"{title_text} を哲学・心理と偉人の言葉で分解し、静かな思考法に落とし込みます。"
        body = summary_line or "考えすぎる夜に“考えない時間”をつくるための小さなステップを紹介。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：深呼吸・メモ・短い無思考タイムを挟む"
        hash_line = " ".join(tags) if tags else "#思考法 #哲学 #夜のラジオ"
        return _normalize_description_length("\n".join(filter(None, [opener, body, audience_line, take_line, hash_line])))

    if channel_code in {"CH04"}:
        opener = f"{title_text} の“違和感/謎”を心理・脳科学・物語で探究し、日常に使える視点に翻訳します。"
        body = summary_line or "静かな語りで“なるほど”を届ける知的エンタメ回です。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：気づいた違和感をメモし、1日1つ観察してみる"
        hash_line = " ".join(tags) if tags else "#心理学 #脳科学 #好奇心 #知的エンタメ"
        return _normalize_description_length("\n".join(filter(None, [opener, body, audience_line, take_line, hash_line])))

    if channel_code in {"CH03"}:
        opener = f"{title_text} を“病院任せにしない”日常習慣で整える方法をまとめました。"
        body = summary_line or "50〜70代の体と心をやさしくケアするシンプルなステップ。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：寝る前の呼吸・短いストレッチ・水分補給"
        hash_line = " ".join(tags) if tags else "#シニア健康 #習慣化 #ウェルネス"
        return _normalize_description_length("\n".join(filter(None, [opener, body, audience_line, take_line, hash_line])))

    if channel_code in {"CH05"}:
        opener = f"{title_text} を安心とユーモアで解説。距離の取り方・伝え方・再出発のヒントを紹介。"
        body = summary_line or "シニア世代の恋愛・パートナーシップを穏やかに進めるための道しるべ。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：短い挨拶・連絡頻度の合意・1つの共通体験を増やす"
        hash_line = " ".join(tags) if tags else "#シニア恋愛 #コミュ力 #第二の人生"
        return _normalize_description_length("\n".join(filter(None, [opener, body, audience_line, take_line, hash_line])))

    if channel_code in {"CH06"}:
        opener = f"{title_text} の“噂”と“根拠”を切り分け、考察で本当かもしれないを探ります。"
        body = summary_line or "ワクワクしつつ冷静に検証する安全運転の都市伝説ガイド。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：複数ソースを照合・仮説と事実を分けてメモ"
        hash_line = " ".join(tags) if tags else "#都市伝説 #考察 #検証"
        return _normalize_description_length("\n".join(filter(None, [opener, body, audience_line, take_line, hash_line])))

    if channel_code in {"CH08"}:
        opener = f"{title_text} を“悪用厳禁”の視点で安全に扱う方法を解説します。"
        body = summary_line or "波動・カルマ・反応しない力を、心理とミニ実験付きで紹介。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"⚠️ 注意：\n{takeaways_block}" if takeaways_block else "⚠️ 注意：無理をせず、体調や人間関係を優先して試してください。"
        hash_line = " ".join(tags) if tags else "#スピリチュアル #波動 #自己浄化"
        return _normalize_description_length("\n".join(filter(None, [opener, body, audience_line, take_line, hash_line])))

    if channel_code in {"CH09"}:
        opener = f"{title_text} を“危険人物/言ってはいけない言葉”の視点で整理し、線引きのチェックリストを提供。"
        body = summary_line or "舐められない距離感と、今日からできる自己防衛の一言。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：言わないリストを作る / 距離を置くサインを1つ決める"
        hash_line = " ".join(tags) if tags else "#人間関係 #自己防衛 #線引き"
        return _normalize_description_length("\n".join(filter(None, [opener, body, audience_line, take_line, hash_line])))

    # fallback
    fallback_lines = [
        f"{title_text} の要点を短くまとめました。",
        summary_line or lead,
        takeaways_block,
        " ".join(tags) if tags else None,
    ]
    return _normalize_description_length("\n".join(filter(None, fallback_lines)))


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
        return len(text)
    except Exception:
        return None


def _ensure_stage_bucket(
    matrix: Dict[str, Dict[str, Dict[str, int]]], channel_code: str, stage_key: str
) -> Dict[str, int]:
    channel_bucket = matrix.setdefault(channel_code, {})
    stage_bucket = channel_bucket.get(stage_key)
    if not stage_bucket:
        stage_bucket = {status: 0 for status in VALID_STAGE_STATUSES}
        stage_bucket["unknown"] = 0
        channel_bucket[stage_key] = stage_bucket
    else:
        for status in VALID_STAGE_STATUSES:
            stage_bucket.setdefault(status, 0)
        stage_bucket.setdefault("unknown", 0)
    return stage_bucket


def _increment_stage_matrix(
    matrix: Dict[str, Dict[str, Dict[str, int]]],
    channel_code: str,
    stages: Dict[str, Any],
) -> None:
    for stage_key in STAGE_ORDER:
        stage_bucket = _ensure_stage_bucket(matrix, channel_code, stage_key)
        stage_entry = stages.get(stage_key)
        status = _stage_status_value(stage_entry)
        stage_bucket[status] = stage_bucket.get(status, 0) + 1


def _collect_alerts(
    *,
    channel_code: str,
    video_number: str,
    stages: Dict[str, Any],
    metadata: Dict[str, Any],
    status_value: str,
    alerts: List[DashboardAlert],
) -> None:
    if status_value == "blocked" or any(
        _stage_status_value(stages.get(stage_key)) == "blocked" for stage_key in STAGE_ORDER
    ):
        alerts.append(
            DashboardAlert(
                type="blocked_stage",
                channel=channel_code,
                video=video_number,
                message="ステージが要対応状態です",
            )
        )

    audio_quality = metadata.get("audio", {}).get("quality", {})
    quality_status = None
    if isinstance(audio_quality, dict):
        quality_status = audio_quality.get("status") or audio_quality.get("label")
    elif isinstance(audio_quality, str):
        quality_status = audio_quality
    if quality_status:
        ok_statuses = {"completed", "ok", "良好", "問題なし", "完了"}
        if all(token.lower() not in ok_statuses for token in [quality_status.lower()]):
            alerts.append(
                DashboardAlert(
                    type="audio_quality",
                    channel=channel_code,
                    video=video_number,
                    message=f"音声品質ステータス: {quality_status}",
                )
            )

    sheets_meta = metadata.get("sheets")
    if isinstance(sheets_meta, dict):
        state = sheets_meta.get("state")
        if state and state.lower() == "failed":
            alerts.append(
                DashboardAlert(
                    type="sheet_sync",
                    channel=channel_code,
                    video=video_number,
                    message="スプレッドシート同期に失敗しました",
                )
            )


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
        assembled_path = base_dir / "content" / "assembled.md"
        if assembled_path.parent.name != "content":
            raise HTTPException(status_code=400, detail="invalid assembled path")
        try:
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
    if NATURAL_COMMAND_MODEL is None:
        raise RuntimeError("LLM model is not available")

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

    provider, model = NATURAL_COMMAND_MODEL
    if provider == "gemini":
        response = model.generate_content(prompt)
        response_text = response.text.strip()
    elif provider == "openrouter":
        client, model_name = model
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "system",
                    "content": "あなたは台本編集アシスタントです。指示をJSONで返してください。",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.3,
            max_tokens=800,
        )
        response_text = response.choices[0].message.content.strip()
    else:  # pragma: no cover - unexpected provider
        raise RuntimeError("Unsupported LLM provider")

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


def _merge_channel_payload(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if value is None:
            continue
        if isinstance(value, dict):
            base_value = merged.get(key)
            if isinstance(base_value, dict):
                combined = dict(base_value)
                combined.update(value)
            else:
                combined = dict(value)
            merged[key] = combined
        else:
            merged[key] = value
    channel_id = merged.get("channel_id")
    if isinstance(channel_id, str):
        merged["channel_id"] = channel_id.upper()
    merged.setdefault("branding", {})
    merged.setdefault("youtube", {})
    return merged


def load_channel_info() -> Dict[str, dict]:
    mapping: Dict[str, dict] = {}
    items: Iterable[dict] = []
    if CHANNEL_INFO_PATH.exists():
        try:
            raw_items = load_json(CHANNEL_INFO_PATH)
            if isinstance(raw_items, list):
                items = raw_items
            elif isinstance(raw_items, dict):
                items = raw_items.values()
            else:
                items = []
        except Exception as exc:  # pragma: no cover - log but continue
            logger.warning("Failed to load %s: %s", CHANNEL_INFO_PATH, exc)
            items = []
    for entry in items:
        code = entry.get("channel_id")
        if not code:
            continue
        mapping[code.upper()] = _merge_channel_payload({"channel_id": code.upper()}, entry)

    if CHANNELS_DIR.exists():
        for child in CHANNELS_DIR.iterdir():
            if not child.is_dir():
                continue
            info_path = child / "channel_info.json"
            if not info_path.exists():
                continue
            try:
                entry = load_json(info_path)
            except Exception as exc:  # pragma: no cover - corrupted channel file
                logger.warning("Failed to parse %s: %s", info_path, exc)
                continue
            channel_code = entry.get("channel_id")
            if not channel_code:
                parts = child.name.split("-", 1)
                channel_code = parts[0].upper() if parts else None
            if not channel_code:
                continue
            existing = mapping.get(channel_code.upper(), {"channel_id": channel_code.upper()})
            mapping[channel_code.upper()] = _merge_channel_payload(existing, entry)
    return mapping


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
    branding = info.get("branding") or {}
    youtube_payload = info.get("youtube") or {}
    synced_at = youtube_payload.get("synced_at") or branding.get("updated_at")
    needs_refresh = not _has_essential_branding(info) or YouTubeDataClient.is_stale(synced_at)
    if not needs_refresh:
        return info
    try:
        ensure_channel_branding(channel_code, info, force_refresh=True, ignore_backoff=False, strict=False)
    except HTTPException:
        return info
    refreshed = refresh_channel_info(force=True).get(channel_code)
    return refreshed or info


def refresh_channel_info(force: bool = False) -> Dict[str, dict]:
    global CHANNEL_INFO, CHANNEL_INFO_MTIME
    try:
        mtime = CHANNEL_INFO_PATH.stat().st_mtime
    except FileNotFoundError:
        mtime = 0.0
    if force or not CHANNEL_INFO or mtime > CHANNEL_INFO_MTIME:
        CHANNEL_INFO = load_channel_info()
        CHANNEL_INFO_MTIME = mtime
    return CHANNEL_INFO


def find_channel_directory(channel_code: str) -> Optional[Path]:
    upper = channel_code.upper()
    if not CHANNELS_DIR.exists():
        return None
    for candidate in CHANNELS_DIR.iterdir():
        if candidate.is_dir() and candidate.name.upper().startswith(f"{upper}-"):
            return candidate
    return None


def persist_channel_entry(channel_code: str, payload: dict) -> None:
    global CHANNEL_INFO_MTIME
    payload["channel_id"] = channel_code.upper()
    with CHANNEL_INFO_LOCK:
        refresh_channel_info()
        CHANNEL_INFO[channel_code.upper()] = payload
        serialized = sorted(
            CHANNEL_INFO.values(),
            key=lambda item: item.get("channel_id", ""),
        )
        CHANNEL_INFO_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CHANNEL_INFO_PATH, "w", encoding="utf-8") as handle:
            json.dump(serialized, handle, ensure_ascii=False, indent=2)
        try:
            CHANNEL_INFO_MTIME = CHANNEL_INFO_PATH.stat().st_mtime
        except FileNotFoundError:
            CHANNEL_INFO_MTIME = 0.0

    channel_dir = find_channel_directory(channel_code)
    if channel_dir:
        info_path = channel_dir / "channel_info.json"
        with open(info_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)


def infer_channel_genre(info: dict) -> Optional[str]:
    genre = info.get("genre")
    if isinstance(genre, str) and genre.strip():
        return genre.strip()

    metadata = info.get("metadata")
    if isinstance(metadata, dict):
        meta_genre = metadata.get("genre")
        if isinstance(meta_genre, str) and meta_genre.strip():
            return meta_genre.strip()

    name = info.get("name")
    if isinstance(name, str):
        for separator in ("　", " "):
            if separator in name:
                candidate = name.split(separator, 1)[0].strip()
                if candidate:
                    return candidate
        stripped = name.strip()
        if stripped:
            return stripped

    return None


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
        or youtube_info.get("url")
        or youtube_info.get("source")
        or info.get("youtube_url")
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
    youtube_payload["synced_at"] = datetime.now(timezone.utc).isoformat()
    info["youtube"].update(youtube_payload)
    YOUTUBE_BRANDING_BACKOFF.pop(channel_code, None)

    persist_channel_entry(channel_code, info)
    refresh_channel_info(force=True)
    return branding_payload


refresh_channel_info(force=True)
init_lock_storage()
CONTENT_PROCESSOR = ContentProcessor(PROJECT_ROOT)


class RequestsOpenRouterClient:
    """Minimal OpenRouter chat client that mirrors the parts of openai.OpenAI we use."""

    def __init__(self, api_key: str, *, base_url: str = "https://openrouter.ai/api/v1", timeout: float = 60.0) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        referer = _load_env_value("OPENROUTER_REFERRER") or os.getenv("OPENROUTER_REFERRER")
        title = _load_env_value("OPENROUTER_TITLE") or os.getenv("OPENROUTER_TITLE")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if referer:
            headers["HTTP-Referer"] = referer
        if title:
            headers["X-Title"] = title
        self.headers = headers
        self.session = requests.Session()
        self.chat = SimpleNamespace(completions=self._ChatCompletions(self))

    class _ChatCompletions:
        def __init__(self, outer: "RequestsOpenRouterClient") -> None:
            self._outer = outer

        def create(
            self,
            *,
            model: str,
            messages: List[Dict[str, object]],
            temperature: float = 0.3,
            max_tokens: int = 800,
        ):
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            response = self._outer.session.post(
                f"{self._outer.base_url}/chat/completions",
                json=payload,
                headers=self._outer.headers,
                timeout=self._outer.timeout,
            )
            if not response.ok:
                raise RuntimeError(f"OpenRouter request failed ({response.status_code}): {response.text}")
            data = response.json()
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:  # pragma: no cover - malformed payload
                raise RuntimeError(f"Unexpected OpenRouter response: {data}") from exc
            if isinstance(content, list):
                text = " ".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
            else:
                text = str(content or "").strip()
            if not text:
                raise RuntimeError("OpenRouter response did not include any content")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            )


def _init_natural_command_model():
    llm = _get_ui_settings().get("llm", {})
    phase_provider, phase_model = _resolve_phase_choice(
        llm,
        "natural_command",
        default_provider="openrouter",
        default_model=_load_env_value("OPENROUTER_COMMAND_MODEL") or NATURAL_COMMAND_DEFAULT_MODEL,
        allowed_providers={"openai", "openrouter"},
    )
    preferred_provider = phase_provider
    preferred_model = phase_model
    # 1) OpenAI優先指定なら、まずOpenAIだけを試す（OpenRouterキー未設定でも通す）
    if preferred_provider == "openai":
        openai_key = _get_effective_openai_key()
        if openai_key and OpenAI is not None:
            try:
                azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
                if azure_endpoint:
                    client = OpenAI(
                        api_key=openai_key,
                        base_url=azure_endpoint.rstrip("/"),
                        default_headers={"api-key": openai_key},
                    )
                else:
                    client = OpenAI(api_key=openai_key)
                logger.info("Natural command LLM initialized (OpenAI): %s", preferred_model)
                return ("openai", (client, preferred_model))
            except Exception as exc:  # pragma: no cover
                logger.warning("Failed to initialize OpenAI for natural command (%s): %s", preferred_model, exc)
        # OpenAI指定だがキーなし/初期化失敗 → OpenRouterへフォールバック

    # 2) OpenRouterを試す（キーが無ければ None を返してヒューリスティックへ）
    try:
        openrouter_key = _get_effective_openrouter_key()
    except Exception as exc:  # pragma: no cover - missing key
        logger.info("Natural command OpenRouter key not available: %s", exc)
        return None

    model_name = preferred_model
    if OpenAI is not None:
        try:
            client = OpenAI(api_key=openrouter_key, base_url="https://openrouter.ai/api/v1")
            logger.info("Natural command LLM initialized (OpenRouter): %s", model_name)
            return ("openrouter", (client, model_name))
        except Exception as exc:  # pragma: no cover - initialization failure
            message = str(exc)
            if isinstance(exc, TypeError) and "proxies" in message:
                logger.info(
                    "OpenAI SDK/httpx mismatch detected; falling back to REST client for %s (%s)",
                    model_name,
                    message,
                )
            else:
                logger.warning("Failed to initialize OpenRouter model via openai SDK (%s): %s", model_name, exc)

    try:
        client = RequestsOpenRouterClient(openrouter_key)
        logger.info("Natural command LLM initialized (OpenRouter REST): %s", model_name)
        return ("openrouter", (client, model_name))
    except Exception as exc:  # pragma: no cover - initialization failure
        logger.error("Failed to initialize OpenRouter REST client (%s): %s", model_name, exc)

    if not openrouter_key:
        logger.info("No LLM API key configured; natural command will use heuristic parser.")
    return None


NATURAL_COMMAND_MODEL = _init_natural_command_model()


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


class OptimisticUpdateRequest(BaseModel):
    expected_updated_at: Optional[str] = Field(None, description="最新バージョンの updated_at 値")


class StageUpdateRequest(OptimisticUpdateRequest):
    stages: Dict[str, StageStatus]


class TextUpdateRequest(OptimisticUpdateRequest):
    content: str
    regenerate_audio: Optional[bool] = Field(None, description="音声と字幕を再生成するか")
    update_assembled: Optional[bool] = Field(None, description="assembled.md も同期更新するか")


class HumanScriptUpdateRequest(OptimisticUpdateRequest):
    assembled_human: Optional[str] = None
    script_audio_human: Optional[str] = None
    audio_reviewed: Optional[bool] = None


class HumanScriptResponse(BaseModel):
    assembled_path: Optional[str] = None
    assembled_content: Optional[str] = None
    assembled_human_path: Optional[str] = None
    assembled_human_content: Optional[str] = None
    script_audio_path: Optional[str] = None
    script_audio_content: Optional[str] = None
    script_audio_human_path: Optional[str] = None
    script_audio_human_content: Optional[str] = None
    audio_reviewed: bool = False
    updated_at: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


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


class ScriptTextResponse(BaseModel):
    path: Optional[str]
    content: str
    updated_at: Optional[str] = None


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


class PlanningSpreadsheetResponse(BaseModel):
    channel: str
    headers: List[str]
    rows: List[List[Optional[str]]]


class PromptSyncTargetResponse(BaseModel):
    path: str
    exists: bool
    checksum: Optional[str] = None
    updated_at: Optional[str] = None


class PromptDocumentSummaryResponse(BaseModel):
    id: str
    label: str
    description: Optional[str] = None
    relative_path: str
    size_bytes: int
    updated_at: Optional[str] = None
    checksum: str
    sync_targets: List[PromptSyncTargetResponse] = Field(default_factory=list)


class PromptDocumentResponse(PromptDocumentSummaryResponse):
    content: str


class PromptUpdateRequest(BaseModel):
    content: str
    expected_checksum: Optional[str] = Field(
        default=None,
        description="前回取得時のチェックサム。整合性チェックに使用する。",
    )

    @field_validator("content")
    @classmethod
    def ensure_string(cls, value: str) -> str:
        if value is None:
            raise ValueError("content is required")
        return value


class BatchWorkflowConfig(BaseModel):
    min_characters: int = Field(8000, ge=1000)
    max_characters: int = Field(12000, ge=1000)
    script_prompt_template: Optional[str] = None
    quality_check_template: Optional[str] = None
    llm_model: Optional[str] = Field(None, description="未指定の場合はシステム設定の 'script_rewrite' モデルを使用")
    loop_mode: bool = True
    auto_retry: bool = True
    debug_log: bool = False


class BatchWorkflowRequest(BaseModel):
    channel_code: str
    video_numbers: List[str]
    config: BatchWorkflowConfig = BatchWorkflowConfig()

    @field_validator("video_numbers")
    @classmethod
    def validate_videos(cls, values: List[str]) -> List[str]:
        cleaned = []
        for value in values:
            value = value.strip()
            if not value.isdigit():
                raise HTTPException(status_code=400, detail="video_numbers には数値のみ指定してください。")
            cleaned.append(value.zfill(3))
        if not cleaned:
            raise HTTPException(status_code=400, detail="video_numbers を1件以上指定してください。")
        return cleaned


class BatchWorkflowTaskResponse(BaseModel):
    task_id: str
    channel_code: str
    video_numbers: List[str]
    status: str
    log_path: Optional[str] = None
    config_path: Optional[str] = None
    created_at: Optional[str] = None
    queue_entry_id: Optional[int] = None


class BatchWorkflowLogResponse(BaseModel):
    task_id: str
    lines: List[str]


class BatchQueueEntryResponse(BaseModel):
    id: int
    channel_code: str
    video_numbers: List[str]
    status: str
    task_id: Optional[str] = None
    created_at: str
    updated_at: str
    processed_count: Optional[int] = None
    total_count: Optional[int] = None
    current_video: Optional[str] = None
    issues: Optional[Dict[str, str]] = None


class BatchQueueRequest(BaseModel):
    channel_code: str
    video_numbers: List[str]
    config: BatchWorkflowConfig = BatchWorkflowConfig()

    @field_validator("video_numbers")
    @classmethod
    def validate_videos(cls, values: List[str]) -> List[str]:
        cleaned = []
        for value in values:
            value = value.strip()
            if not value.isdigit():
                raise HTTPException(status_code=400, detail="video_numbers には数値のみ指定してください。")
            cleaned.append(value.zfill(3))
        if not cleaned:
            raise HTTPException(status_code=400, detail="video_numbers を1件以上指定してください。")
        return cleaned


class VideoDetailResponse(BaseModel):
    channel: str
    video: str
    script_id: Optional[str]
    title: Optional[str]
    status: str
    ready_for_audio: bool
    stages: Dict[str, str]
    redo_script: bool = True
    redo_audio: bool = True
    redo_note: Optional[str] = None
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


class RedoItemResponse(BaseModel):
    channel: str
    video: str
    redo_script: bool
    redo_audio: bool
    redo_note: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None


class RedoSummaryItem(BaseModel):
    channel: str
    redo_script: int
    redo_audio: int
    redo_both: int


class ThumbnailOverrideRequest(BaseModel):
    thumbnail_url: str
    thumbnail_path: Optional[str] = None


class ThumbnailOverrideResponse(BaseModel):
    status: str
    thumbnail_url: str
    thumbnail_path: Optional[str] = None
    updated_at: str




class AudioIntegrityItem(BaseModel):
    channel: str
    video: str
    missing: List[str]
    audio_path: Optional[str] = None
    srt_path: Optional[str] = None
    b_text_path: Optional[str] = None
    audio_duration: Optional[float] = None
    srt_duration: Optional[float] = None
    duration_diff: Optional[float] = None


class VoicevoxKanaDiff(BaseModel):
    engine_kana: str = ""
    llm_kana: str = ""
    diff: List[Any] = Field(default_factory=list)


class AudioAnalysisResponse(BaseModel):
    channel: str
    video: str
    b_text_with_pauses: Optional[str] = None
    pause_map: Optional[List[Any]] = None
    voicevox_kana: Optional[str] = None
    voicevox_kana_corrected: Optional[str] = None
    voicevox_kana_diff: Optional[VoicevoxKanaDiff] = None
    voicevox_kana_llm_ref: Optional[Any] = None
    voicevox_accent_phrases: Optional[Any] = None
    warnings: List[str] = Field(default_factory=list)


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


class VideoSummaryResponse(BaseModel):
    video: str
    script_id: Optional[str]
    title: Optional[str]
    status: str
    ready_for_audio: bool
    stages: Dict[str, str]
    updated_at: Optional[str] = None
    character_count: Optional[int] = None
    planning: Optional[PlanningInfoResponse] = None
    youtube_description: Optional[str] = None


class PlanningRequirementSummary(BaseModel):
    min_no: Optional[int] = Field(None, description="適用される No. の下限（None の場合は全件）")
    required_keys: List[str] = Field(default_factory=list, description="optional_fields_registry のキー一覧")
    required_columns: List[str] = Field(default_factory=list, description="channels CSV 上の列名一覧")


class ChannelProfileResponse(BaseModel):
    channel_code: str
    channel_name: Optional[str] = None
    audience_profile: Optional[str] = None
    persona_summary: Optional[str] = None
    script_prompt: Optional[str] = None
    description: Optional[str] = None
    default_tags: Optional[List[str]] = None
    youtube_title: Optional[str] = None
    youtube_description: Optional[str] = None
    youtube_handle: Optional[str] = None
    audio_default_voice_key: Optional[str] = None
    audio_section_voice_rules: Dict[str, str] = Field(default_factory=dict)
    default_min_characters: int = Field(8000, ge=1000)
    default_max_characters: int = Field(12000, ge=1000)
    llm_model: str = Field("qwen/qwen3-14b:free", description="OpenRouter model ID used for量産")
    quality_check_template: Optional[str] = None
    planning_persona: Optional[str] = Field(
        None, description="SSOT のチャンネル共通ペルソナ（channels CSV のターゲット層に使用）"
    )
    planning_persona_path: Optional[str] = Field(
        None, description="SSOT persona ドキュメントのパス（相対）"
    )
    planning_required_fieldsets: List[PlanningRequirementSummary] = Field(
        default_factory=list, description="企画シートで必須となる列の要件"
    )
    planning_description_defaults: Dict[str, str] = Field(
        default_factory=dict,
        description="説明文_リード / 説明文_この動画でわかること の既定値",
    )
    planning_template_path: Optional[str] = Field(
        None, description="channel 用 planning テンプレ CSV のパス"
    )
    planning_template_headers: List[str] = Field(
        default_factory=list, description="テンプレ CSV のヘッダー行"
    )
    planning_template_sample: List[str] = Field(
        default_factory=list, description="テンプレ CSV サンプル行（2行目）"
    )


class PersonaDocumentResponse(BaseModel):
    channel: str
    path: str
    content: str


class PersonaDocumentUpdateRequest(BaseModel):
    content: str = Field(..., min_length=1, description="ペルソナドキュメント全文")


class PlanningTemplateResponse(BaseModel):
    channel: str
    path: str
    content: str
    headers: List[str]
    sample: List[str]


class PlanningTemplateUpdateRequest(BaseModel):
    content: str = Field(..., min_length=1, description="planningテンプレ CSV 全文")


class ChannelProfileUpdateAudio(BaseModel):
    default_voice_key: Optional[str] = Field(
        None, description="audio/channels/CHxx/voice_config.json 内の voice preset key"
    )
    section_voice_rules: Optional[Dict[str, str]] = Field(
        None, description="セクション別に適用する voice preset のマッピング"
    )


class ChannelProfileUpdateRequest(BaseModel):
    script_prompt: Optional[str] = Field(None, description="Qwen台本プロンプト全文")
    description: Optional[str] = Field(None, description="チャンネル説明文")
    youtube_title: Optional[str] = Field(None, description="YouTube上のチャンネルタイトル")
    youtube_description: Optional[str] = Field(None, description="YouTube上の説明文 / 投稿テンプレ")
    youtube_handle: Optional[str] = Field(None, description="YouTubeハンドル (@name)")
    default_tags: Optional[List[str]] = Field(
        None, description="投稿時に使うデフォルトタグ（配列）"
    )
    audio: Optional[ChannelProfileUpdateAudio] = None

class ChannelBranding(BaseModel):
    avatar_url: Optional[str] = None
    banner_url: Optional[str] = None
    title: Optional[str] = None
    subscriber_count: Optional[int] = None
    view_count: Optional[int] = None
    video_count: Optional[int] = None
    custom_url: Optional[str] = None
    handle: Optional[str] = None
    url: Optional[str] = None
    launch_date: Optional[str] = None
    theme_color: Optional[str] = None
    updated_at: Optional[str] = None


class ChannelSummaryResponse(BaseModel):
    code: str
    name: Optional[str] = None
    description: Optional[str] = None
    video_count: int = 0
    branding: Optional[ChannelBranding] = None
    spreadsheet_id: Optional[str] = None
    youtube_title: Optional[str] = None
    youtube_handle: Optional[str] = None
    genre: Optional[str] = None


class LockMetricSample(BaseModel):
    timestamp: str
    type: str
    timeout: int
    unexpected: int


class LockMetricsResponse(BaseModel):
    timeout: int
    unexpected: int
    history: List[LockMetricSample]
    daily: List["LockMetricsDailySummary"]


class LockMetricsDailySummary(BaseModel):
    date: str
    timeout: int
    unexpected: int


LockMetricsResponse.model_rebuild()




class DashboardChannelSummary(BaseModel):
    code: str
    total: int = 0
    script_completed: int = 0
    audio_completed: int = 0
    srt_completed: int = 0
    blocked: int = 0
    ready_for_audio: int = 0
    pending_sync: int = 0


class DashboardAlert(BaseModel):
    type: str
    channel: str
    video: str
    message: str
    updated_at: Optional[str] = None


class DashboardOverviewResponse(BaseModel):
    generated_at: str
    channels: List[DashboardChannelSummary]
    stage_matrix: Dict[str, Dict[str, Dict[str, int]]]
    alerts: List[DashboardAlert]


class WorkflowPrecheckItem(BaseModel):
    script_id: str
    video_number: str
    progress: Optional[str] = None
    title: Optional[str] = None
    flag: Optional[str] = None


class WorkflowPrecheckPendingSummary(BaseModel):
    channel: str
    count: int
    items: List[WorkflowPrecheckItem]


class WorkflowPrecheckReadyEntry(BaseModel):
    channel: str
    video_number: str
    script_id: str
    audio_status: Optional[str] = None


class WorkflowPrecheckResponse(BaseModel):
    generated_at: str
    pending: List[WorkflowPrecheckPendingSummary]
    ready: List[WorkflowPrecheckReadyEntry]


class ThumbnailVariantResponse(BaseModel):
    id: str
    label: Optional[str] = None
    status: Optional[str] = None
    image_url: Optional[str] = None
    image_path: Optional[str] = None
    preview_url: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
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


class ThumbnailLibraryImportRequest(BaseModel):
    url: str = Field(..., min_length=1)
    file_name: Optional[str] = None


class ThumbnailDescriptionResponse(BaseModel):
    description: str
    model: Optional[str] = None
    source: Literal["openai", "openrouter", "heuristic"]


class LLMConfig(BaseModel):
    caption_provider: str = "openai"
    openai_api_key: Optional[str] = None
    openai_caption_model: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    openrouter_caption_model: Optional[str] = None
    openai_key_configured: bool
    openrouter_key_configured: bool
    openai_models: List[str]
    openrouter_models: List[str]
    openai_key_preview: Optional[str] = None
    openrouter_key_preview: Optional[str] = None
    openai_models_error: Optional[str] = None
    openrouter_models_error: Optional[str] = None
    phase_models: Dict[str, Dict[str, Any]]
    phase_details: Optional[Dict[str, Dict[str, Any]]] = None


class LLMSettingsResponse(BaseModel):
    llm: LLMConfig


class LLMSettingsUpdate(BaseModel):
    caption_provider: Optional[Literal["openai", "openrouter"]] = None
    openai_api_key: Optional[str] = None
    openai_caption_model: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    openrouter_caption_model: Optional[str] = None
    phase_models: Optional[Dict[str, Dict[str, object]]] = None


class LlmMetric(BaseModel):
    name: str
    value: float
    source: Optional[str] = None


class LlmModelInfo(BaseModel):
    id: str
    label: str
    provider: str
    model_id: Optional[str] = None
    iq: int
    knowledge_metric: LlmMetric
    specialist_metric: LlmMetric
    notes: Optional[str] = None
    last_updated: Optional[str] = None


def _coerce_video_from_dir(name: str) -> Optional[str]:
    if not name:
        return None
    match = re.match(r"(\d+)", name.strip())
    if not match:
        return None
    return match.group(1).zfill(3)


def _thumbnail_asset_roots(channel_code: str) -> List[Path]:
    roots: List[Path] = []
    channel_dir = find_channel_directory(channel_code)
    if channel_dir:
        roots.append(channel_dir / "thumbnails")
    roots.append(THUMBNAIL_ASSETS_DIR / channel_code)
    return roots


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


def _append_disk_variants(project: ThumbnailProjectResponse, disk_variants: List[ThumbnailVariantResponse]) -> None:
    if not disk_variants:
        return
    existing_keys = set()
    for variant in project.variants:
        existing_keys.update(_variant_identity_keys(variant))
    for variant in disk_variants:
        identity_keys = _variant_identity_keys(variant)
        if identity_keys & existing_keys:
            continue
        existing_keys.update(identity_keys)
        project.variants.append(variant)


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
    # Legacy/fallback: allow dropping images under /thumbnails/CHXX* directories (without /assets)
    fallback_root = PROJECT_ROOT / "thumbnails"
    if fallback_root.exists():
        for child in fallback_root.iterdir():
            if not child.is_dir():
                continue
            name_upper = child.name.upper()
            if not name_upper.startswith(channel_code.upper()):
                continue
            if child not in dirs:
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


def _describe_image_with_openrouter(
    image_path: Path, api_key: str, preferred_model: Optional[str] = None
) -> tuple[str, str]:
    if not api_key:
        logger.error("OpenRouter API key is not configured; cannot caption %s", image_path)
        raise HTTPException(status_code=503, detail="OpenRouter APIキーが未設定のため画像説明を生成できません。")
    try:
        raw_bytes = image_path.read_bytes()
    except OSError as exc:
        logger.warning("Failed to read image for caption: %s", exc)
        raise HTTPException(status_code=500, detail=f"画像の読み込みに失敗しました: {exc}")
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    data_url = f"data:{mime_type};base64,{base64.b64encode(raw_bytes).decode('ascii')}"
    prompt = (
        "以下のYouTubeサムネイル画像の内容を80文字前後の日本語で説明してください。"
        "人物・背景・文字・雰囲気を具体的に触れてください。"
    )
    configured_model = (preferred_model or _load_env_value("THUMBNAIL_CAPTION_MODEL") or "").strip()
    caption_models: List[str] = []
    if configured_model:
        caption_models.append(configured_model)
    if THUMBNAIL_CAPTION_DEFAULT_MODEL not in caption_models:
        caption_models.append(THUMBNAIL_CAPTION_DEFAULT_MODEL)
    for fallback in THUMBNAIL_CAPTION_FALLBACK_MODELS:
        if fallback not in caption_models:
            caption_models.append(fallback)
    base_payload = {
        "max_tokens": 300,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    invalid_models: List[tuple[str, str]] = []
    for model_name in caption_models:
        payload = dict(base_payload)
        payload["model"] = model_name
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=60,
            )
        except requests.RequestException as exc:  # pragma: no cover - network failure
            logger.error("OpenRouter captioning request failed (%s): %s", model_name, exc)
            raise HTTPException(status_code=502, detail=f"OpenRouter captioning failed: {exc}") from exc

        if response.ok:
            try:
                data = response.json()
            except ValueError as exc:
                logger.error("OpenRouter caption response not JSON for %s: %s", model_name, exc)
                raise HTTPException(status_code=502, detail="OpenRouter応答が無効でした。") from exc

            try:
                content = data["choices"][0]["message"]["content"]
                if isinstance(content, list):
                    text = " ".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
                else:
                    text = str(content).strip()
            except (KeyError, IndexError, AttributeError) as exc:
                logger.error("OpenRouter caption response malformed (%s): %s", model_name, data)
                raise HTTPException(status_code=502, detail="OpenRouter応答が不正です。") from exc

            if not text:
                raise HTTPException(status_code=502, detail="OpenRouter応答に説明文が含まれていません。")
            return text, model_name

        detail_text = response.text
        lowered = detail_text.lower()
        if response.status_code in (400, 404) and (
            "not a valid model" in lowered or "no endpoints found" in lowered
        ):
            logger.warning("Thumbnail caption model %s rejected: %s", model_name, detail_text)
            invalid_models.append((model_name, detail_text))
            continue
        try:
            response.raise_for_status()
        except requests.RequestException:
            logger.error("OpenRouter captioning failed (%s): %s", model_name, detail_text)
            raise HTTPException(status_code=502, detail=f"OpenRouter captioning failed: {detail_text}") from None

    summary = "; ".join(f"{model}: {detail}" for model, detail in invalid_models) or "no caption model accepted"
    logger.error("All thumbnail caption model candidates failed: %s", summary)
    raise HTTPException(
        status_code=502,
        detail=f"OpenRouter captioning failed for all models ({summary}).",
    )


def _describe_image_with_openai(image_path: Path, api_key: str, model_name: str) -> tuple[str, str]:
    if not OpenAI:
        raise HTTPException(status_code=503, detail="OpenAI SDK がインストールされていません。")
    try:
        raw_bytes = image_path.read_bytes()
    except OSError as exc:
        logger.warning("Failed to read image for caption: %s", exc)
        raise HTTPException(status_code=500, detail=f"画像の読み込みに失敗しました: {exc}") from exc
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    data_url = f"data:{mime_type};base64,{base64.b64encode(raw_bytes).decode('ascii')}"
    prompt = (
        "以下のYouTubeサムネイル画像の内容を80文字前後の日本語で説明してください。"
        "人物・背景・文字・雰囲気を具体的に触れてください。"
    )
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    if azure_endpoint:
        client = OpenAI(
            api_key=api_key,
            base_url=azure_endpoint.rstrip("/"),
            default_headers={"api-key": api_key},
        )
    else:
        client = OpenAI(api_key=api_key)
    try:
        response = client.chat.completions.create(
            model=model_name,
            max_tokens=400,
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": "あなたはYouTubeサムネイルの要約者です。短く具体的に記述してください。",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        )
    except Exception as exc:  # pragma: no cover - network/SDK errors
        logger.error("OpenAI captioning request failed (%s): %s", model_name, exc)
        raise HTTPException(status_code=502, detail=f"OpenAI captioning failed: {exc}") from exc

    choice = response.choices[0].message.content
    if isinstance(choice, list):
        text = " ".join(part.get("text", "") for part in choice if isinstance(part, dict)).strip()
    else:
        text = str(choice or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="OpenAI応答に説明文が含まれていません。")
    return text, model_name


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
app.include_router(jobs.router)
app.include_router(swap.router)
app.include_router(params.router)
try:
    from backend.routers import tts_progress
    app.include_router(tts_progress.router)
except Exception as e:
    logger.error("Failed to load tts_progress router: %s", e)
try:
    from backend.routers import research_files

    app.include_router(research_files.router)
except Exception as e:
    logger.error("Failed to load research_files router: %s", e)

try:
    from backend.routers import agent_org

    app.include_router(agent_org.router)
except Exception as e:
    logger.error("Failed to load agent_org router: %s", e)

# 静的に thumbnails ディレクトリを配信
thumb_dir = PROJECT_ROOT / "thumbnails"
if thumb_dir.exists():
    app.mount("/thumbnails", StaticFiles(directory=thumb_dir), name="thumbnails")


@app.get("/api/redo/summary", response_model=List[RedoSummaryItem])
def get_redo_summary(channel: Optional[str] = None):
    """チャンネル別のリテイク件数サマリを返す。channel を指定しない場合は全チャンネル集計。"""
    def _list_progress_channels() -> List[str]:
        base = PROJECT_ROOT / "progress" / "channels"
        if not base.exists():
            return []
        return [p.stem for p in base.glob("*.csv")]

    def progress_csv_rows(channel_code: str) -> List[Dict[str, str]]:
        path = PROJECT_ROOT / "progress" / "channels" / f"{channel_code}.csv"
        if not path.exists():
            return []
        import csv
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return [row for row in reader]

    rows = []
    if channel:
        ch = normalize_channel_code(channel)
        rows = progress_csv_rows(ch)
    else:
        for ch in _list_progress_channels():
            try:
                rows.extend(progress_csv_rows(ch))
            except Exception:
                continue
    summary: Dict[str, Dict[str, int]] = {}
    for row in rows:
        ch = row.get("チャンネル") or row.get("channel") or row.get("Channel") or ""
        if not ch:
            continue
        s_flag = row.get("redo_script", "true").lower()
        a_flag = row.get("redo_audio", "true").lower()
        redo_s = s_flag not in ["false", "0", "no", "n"]
        redo_a = a_flag not in ["false", "0", "no", "n"]
        bucket = summary.setdefault(ch, {"redo_script": 0, "redo_audio": 0, "redo_both": 0})
        if redo_s and redo_a:
            bucket["redo_both"] += 1
        if redo_s:
            bucket["redo_script"] += 1
        if redo_a:
            bucket["redo_audio"] += 1
    return [
        RedoSummaryItem(
            channel=ch,
            redo_script=data["redo_script"],
            redo_audio=data["redo_audio"],
            redo_both=data["redo_both"],
        )
        for ch, data in summary.items()
    ]


@app.get("/api/thumbnails/lookup")
def thumbnail_lookup(
    channel: str = Query(..., description="CHコード (例: CH02)"),
    video: Optional[str] = Query(None, description="動画番号 (例: 019)"),
    title: Optional[str] = Query(None, description="動画タイトル（任意）"),
    limit: int = Query(3, description="返す件数"),
):
    channel_code = normalize_channel_code(channel)
    video_no = normalize_video_number(video) if video else None
    thumbs = _find_thumbnails(channel_code, video_no, title, limit=limit)
    return {"items": thumbs}
try:
    from backend.routers import auto_draft

    app.include_router(auto_draft.router)
except Exception as e:
    logger.error("Failed to load auto_draft router: %s", e)

# Remotion preview restart (best-effort, local only)
@app.post("/api/remotion/restart_preview")
def restart_remotion_preview(port: int = 3100):
    preview_cmd = ["pkill", "-f", "remotion preview"]
    start_cmd = [
        "bash",
        "-lc",
        f"cd {PROJECT_ROOT}/remotion && BROWSER=none npx remotion preview --entry src/index.ts --root . --public-dir public --port {port} >/dev/null 2>&1 &",
    ]
    try:
        subprocess.run(preview_cmd, check=False)
    except Exception as e:
        logger.warning("Failed to kill remotion preview: %s", e)
    try:
        subprocess.run(start_cmd, check=False)
    except Exception as e:
        logger.error("Failed to start remotion preview: %s", e)
        raise HTTPException(status_code=500, detail=f"start failed: {e}")
    return {"status": "ok", "port": port}

def _collect_health_components() -> Dict[str, bool]:
    components: Dict[str, bool] = {
        "project_root": PROJECT_ROOT.exists(),
        "data_dir": DATA_ROOT.exists(),
        "commentary_01": COMMENTARY01_ROOT.exists(),
        "commentary_02": COMMENTARY02_ROOT.exists(),
        "logs_dir": LOGS_ROOT.exists(),
        "ui_log_dir": UI_LOG_DIR.exists(),
                "channel_planning_dir": CHANNEL_PLANNING_DIR.exists(),
        "gemini_api_key": bool(os.getenv("GEMINI_API_KEY")),
        "openrouter_api_key": bool(_get_effective_openrouter_key()),
        "openai_api_key": bool(_get_effective_openai_key()),
    }
    return components


@app.get("/api/healthz")
def healthcheck():
    components = _collect_health_components()
    issues = [name for name, ok in components.items() if not ok]
    status = "ok" if not issues else "degraded"
    return {
        "status": status,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "issues": issues,
        "components": components,
    }

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/prompts", response_model=List[PromptDocumentSummaryResponse])
def list_prompt_documents() -> List[PromptDocumentSummaryResponse]:
    specs = sorted(_load_prompt_documents().values(), key=lambda item: item.get("label", item["id"]))
    return [PromptDocumentSummaryResponse(**_build_prompt_document_payload(spec, include_content=False)) for spec in specs]


@app.get("/api/settings/llm", response_model=LLMSettingsResponse)
def get_llm_settings():
    return _build_llm_settings_response()


@app.put("/api/settings/llm", response_model=LLMSettingsResponse)
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


@app.get("/api/llm/models", response_model=List[LlmModelInfo])
def list_llm_models() -> List[LlmModelInfo]:
    return _load_llm_model_scores()


@app.get("/api/prompts/{prompt_id}", response_model=PromptDocumentResponse)
def fetch_prompt_document(prompt_id: str) -> PromptDocumentResponse:
    spec = _get_prompt_spec(prompt_id)
    payload = _build_prompt_document_payload(spec, include_content=True)
    return PromptDocumentResponse(**payload)


@app.put("/api/prompts/{prompt_id}", response_model=PromptDocumentResponse)
def update_prompt_document(prompt_id: str, payload: PromptUpdateRequest) -> PromptDocumentResponse:
    spec = _get_prompt_spec(prompt_id)
    current = _build_prompt_document_payload(spec, include_content=True)
    expected = payload.expected_checksum
    if expected and expected != current["checksum"]:
        raise HTTPException(status_code=409, detail="他のユーザーが先に更新しました。最新内容を読み込み直してください。")
    previous_content: str = current["content"]
    _persist_prompt_document(spec, new_content=payload.content, previous_content=previous_content)
    logger.info("Prompt %s updated via UI", prompt_id)
    refreshed = _build_prompt_document_payload(spec, include_content=True)
    return PromptDocumentResponse(**refreshed)


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
    return ChannelSummaryResponse(
        code=code,
        name=info.get("name"),
        description=info.get("description"),
        video_count=len(list_video_dirs(code)),
        branding=branding,
        spreadsheet_id=info.get("spreadsheet_id"),
        youtube_title=info.get("youtube", {}).get("title"),
        youtube_handle=info.get("youtube", {}).get("handle"),
        genre=infer_channel_genre(info),
    )


@app.get("/api/channels", response_model=List[ChannelSummaryResponse])
def list_channels():
    channels = []
    channel_info_map = refresh_channel_info(force=True)
    for channel_dir in list_channel_dirs():
        code = channel_dir.name.upper()
        info = channel_info_map.get(code, {"channel_id": code})
        if YOUTUBE_CLIENT is not None:
            info = _ensure_youtube_metrics(code, info)
            channel_info_map[code] = info
        channels.append(_build_channel_summary(code, info))
    return channels


@app.get("/api/planning", response_model=List[PlanningCsvRowResponse])
def list_planning_rows(channel: Optional[str] = Query(None, description="CHコード (例: CH06)")):
    channel_code = normalize_channel_code(channel) if channel else None
    return _load_planning_rows(channel_code)


@app.post("/api/planning/refresh")
def refresh_planning_store(
    channel: Optional[str] = Query(None, description="CHコード (省略可)"),
):
    """
    planning_store を強制再読込する。外部でCSVを編集した直後の手動同期用。
    """
    planning_store.refresh(force=True)
    if channel:
        try:
            normalize_channel_code(channel)
        except Exception:
            pass
    return {"ok": True}


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
    planning_store.refresh(force=True)

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


@app.post("/api/channels/{channel}/branding/refresh", response_model=ChannelSummaryResponse)
def refresh_channel_branding(channel: str, ignore_backoff: bool = Query(False, description="true で一時停止中でも強制実行")):
    channel_code = normalize_channel_code(channel)
    channel_info_map = refresh_channel_info()
    info = channel_info_map.get(channel_code)
    if not info:
        raise HTTPException(status_code=404, detail=f"チャンネル {channel_code} の情報が見つかりません")
    ensure_channel_branding(
        channel_code,
        info,
        force_refresh=True,
        ignore_backoff=ignore_backoff,
        strict=True,
    )
    refreshed = refresh_channel_info(force=True).get(channel_code, info)
    return _build_channel_summary(channel_code, refreshed)


@app.get("/api/channels/{channel}/profile", response_model=ChannelProfileResponse)
def get_channel_profile(channel: str):
    channel_code = normalize_channel_code(channel)
    return _build_channel_profile_response(channel_code)


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
        new_handle = payload.youtube_handle.strip()
        if youtube_info.get("handle") != new_handle:
            _record_change(changes, "youtube.handle", youtube_info.get("handle"), new_handle)
            youtube_info["handle"] = new_handle
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


@app.get("/api/ssot/persona/{channel}", response_model=PersonaDocumentResponse)
def get_persona_document(channel: str):
    channel_code = normalize_channel_code(channel)
    path = _persona_doc_path(channel_code)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{channel_code} のペルソナファイルが見つかりません。")
    content = path.read_text(encoding="utf-8")
    return PersonaDocumentResponse(channel=channel_code, path=_relative_path(path), content=content)


@app.put("/api/ssot/persona/{channel}", response_model=PersonaDocumentResponse)
def update_persona_document(channel: str, payload: PersonaDocumentUpdateRequest):
    channel_code = normalize_channel_code(channel)
    path = _persona_doc_path(channel_code)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{channel_code} のペルソナファイルが見つかりません。")
    content = payload.content
    if not content.strip():
        raise HTTPException(status_code=400, detail="内容を入力してください。")
    if not content.endswith("\n"):
        content += "\n"
    write_text_with_lock(path, content)
    planning_requirements.clear_persona_cache()
    return PersonaDocumentResponse(channel=channel_code, path=_relative_path(path), content=content)


@app.get("/api/ssot/templates/{channel}", response_model=PlanningTemplateResponse)
def get_planning_template(channel: str):
    channel_code = normalize_channel_code(channel)
    path = _planning_template_path(channel_code)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{channel_code} のテンプレートCSVが見つかりません。")
    content = path.read_text(encoding="utf-8")
    headers, sample = _preview_csv_content(content)
    return PlanningTemplateResponse(
        channel=channel_code,
        path=_relative_path(path),
        content=content,
        headers=headers,
        sample=sample,
    )


@app.put("/api/ssot/templates/{channel}", response_model=PlanningTemplateResponse)
def update_planning_template(channel: str, payload: PlanningTemplateUpdateRequest):
    channel_code = normalize_channel_code(channel)
    path = _planning_template_path(channel_code)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{channel_code} のテンプレートCSVが見つかりません。")
    content = payload.content
    headers, sample = _preview_csv_content(content)
    required_columns = _collect_required_columns(channel_code)
    if required_columns:
        missing = [column for column in required_columns if column not in headers]
        if missing:
            joined = ", ".join(missing)
            raise HTTPException(status_code=400, detail=f"テンプレートに必須列が不足しています: {joined}")
    write_text_with_lock(path, content if content.endswith("\n") else content + "\n")
    return PlanningTemplateResponse(
        channel=channel_code,
        path=_relative_path(path),
        content=content if content.endswith("\n") else content + "\n",
        headers=headers,
        sample=sample,
    )


@app.get("/api/dashboard/overview", response_model=DashboardOverviewResponse)
def dashboard_overview(
    channels: Optional[str] = Query(None, description="カンマ区切りのチャンネルコード"),
    status: Optional[str] = Query(None, description="カンマ区切りの案件ステータス"),
    from_param: Optional[str] = Query(None, alias="from", description="更新日時の下限 ISO8601"),
    to_param: Optional[str] = Query(None, alias="to", description="更新日時の上限 ISO8601"),
):
    channel_filter = {code.strip().upper() for code in channels.split(",")} if channels else None
    status_filter = {value.strip() for value in status.split(",") if value.strip()} if status else None
    from_dt = parse_iso_datetime(from_param)
    to_dt = parse_iso_datetime(to_param)

    overview_channels: List[DashboardChannelSummary] = []
    stage_matrix: Dict[str, Dict[str, Dict[str, int]]] = {}
    alerts: List[DashboardAlert] = []

    for channel_dir in list_channel_dirs():
        channel_code = channel_dir.name.upper()
        if channel_filter and channel_code not in channel_filter:
            continue

        summary = DashboardChannelSummary(code=channel_code)
        video_dirs = list_video_dirs(channel_code)

        for video_dir in video_dirs:
            video_number = video_dir.name
            try:
                status_payload = load_status(channel_code, video_number)
            except HTTPException as exc:  # pragma: no cover - unexpected errors propagate
                if exc.status_code == 404:
                    continue
                raise

            base_dir = video_dir  # path to script root
            status_value = status_payload.get("status", "unknown")
            if status_filter and status_value not in status_filter:
                continue

            updated_at_raw = status_payload.get("updated_at")
            updated_at_dt = parse_iso_datetime(updated_at_raw)
            if from_dt and (not updated_at_dt or updated_at_dt < from_dt):
                continue
            if to_dt and (not updated_at_dt or updated_at_dt > to_dt):
                continue

            summary.total += 1
            stages = status_payload.get("stages", {})
            metadata = status_payload.get("metadata", {}) if isinstance(status_payload.get("metadata", {}), dict) else {}

            # 台本完成: script_polish_ai があれば優先、なければ script_review/script_validation を代用
            if _stage_status_value(stages.get("script_polish_ai")) == "completed" or _stage_status_value(
                stages.get("script_review")
            ) == "completed" or _stage_status_value(stages.get("script_validation")) == "completed":
                summary.script_completed += 1

            # 音声完了: audio_synthesis があればそれ、無ければ最終WAVの存在で代用
            audio_done = _stage_status_value(stages.get("audio_synthesis")) == "completed"
            if not audio_done:
                audio_path = resolve_audio_path(status_payload, base_dir)
                audio_done = bool(audio_path and audio_path.exists())
            if audio_done:
                summary.audio_completed += 1

            # 字幕完了: srt_generation があればそれ、無ければ最終SRTの存在で代用
            srt_done = _stage_status_value(stages.get("srt_generation")) == "completed"
            if not srt_done:
                srt_path = resolve_srt_path(status_payload, base_dir)
                srt_done = bool(srt_path and srt_path.exists())
            if srt_done:
                summary.srt_completed += 1

            if status_value == "blocked" or any(
                _stage_status_value(stages.get(stage_key)) == "blocked" for stage_key in STAGE_ORDER
            ):
                summary.blocked += 1

            if bool(metadata.get("ready_for_audio")):
                summary.ready_for_audio += 1

            sheets_meta = metadata.get("sheets") if isinstance(metadata.get("sheets"), dict) else None
            if sheets_meta:
                state = sheets_meta.get("state")
                if state and state.lower() != "synced":
                    summary.pending_sync += 1

            _increment_stage_matrix(stage_matrix, channel_code, stages)
            _collect_alerts(
                channel_code=channel_code,
                video_number=video_number,
                stages=stages,
                metadata=metadata,
                status_value=status_value,
                alerts=alerts,
            )

        if summary.total > 0:
            overview_channels.append(summary)

    return DashboardOverviewResponse(
        generated_at=current_timestamp(),
        channels=overview_channels,
        stage_matrix=stage_matrix,
        alerts=alerts,
    )


@app.get("/api/guards/workflow-precheck", response_model=WorkflowPrecheckResponse)
def workflow_precheck_summary(
    channel: Optional[str] = Query(None, description="CHコードで絞り込み"),
    limit: int = Query(5, ge=1, le=50, description="各チャンネルで返す pending アイテム数"),
) -> WorkflowPrecheckResponse:
    channel_filter = channel.upper() if channel else None
    pending_summaries = workflow_precheck_tools.gather_pending(
        channel_codes=[channel_filter] if channel_filter else None,
        limit=limit,
    )
    ready_entries = workflow_precheck_tools.collect_ready_for_audio(channel_code=channel_filter)

    def _pick(row: Dict[str, Any], *keys: str) -> Optional[str]:
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    pending_payload: List[WorkflowPrecheckPendingSummary] = []
    for summary in pending_summaries:
        normalized_items: List[WorkflowPrecheckItem] = []
        for row in summary.items:
            video_number = _pick(row, "video_number", "動画番号", "動画ID", "No.") or ""
            script_id = _pick(row, "script_id", "台本番号")
            if not script_id:
                script_id = f"{summary.channel}-{video_number}".rstrip("-") or summary.channel
            normalized_items.append(
                WorkflowPrecheckItem(
                    script_id=script_id,
                    video_number=video_number,
                    progress=_pick(row, "progress", "進捗"),
                    title=_pick(row, "title", "タイトル"),
                    flag=_pick(row, "flag", "creation_flag", "作成フラグ"),
                )
            )
        pending_payload.append(
            WorkflowPrecheckPendingSummary(
                channel=summary.channel,
                count=summary.count,
                items=normalized_items,
            )
        )

    ready_payload = [
        WorkflowPrecheckReadyEntry(
            channel=item.channel,
            video_number=item.video_number,
            script_id=item.script_id,
            audio_status=item.audio_status,
        )
        for item in ready_entries
    ]

    return WorkflowPrecheckResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        pending=pending_payload,
        ready=ready_payload,
    )


@app.post("/api/channels/{channel}/videos/{video}/tts/replace", response_model=TtsReplaceResponse)
def replace_tts_segment(channel: str, video: str, payload: TtsReplaceRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)

    base_dir = video_base_dir(channel_code, video_number)
    # 正規パスのみ許可（フォールバック禁止）
    plain_path = base_dir / "audio_prep" / "script_sanitized.txt"
    tagged_path = base_dir / "audio_prep" / "script_sanitized_with_pauses.txt"

    if not plain_path.exists():
        raise HTTPException(status_code=404, detail="script_sanitized.txt not found")

    old_plain = resolve_text_file(plain_path) or ""
    old_tagged = resolve_text_file(tagged_path)

    new_plain, replaced = replace_text(old_plain, payload.original, payload.replacement, payload.scope)
    if replaced == 0:
        raise HTTPException(status_code=400, detail="指定した文字列は音声用テキスト内に見つかりません。")

    timestamp = current_timestamp()
    status["updated_at"] = timestamp

    metadata = status.setdefault("metadata", {})
    audio_meta_raw = metadata.get("audio")
    if not isinstance(audio_meta_raw, dict):
        audio_meta_raw = {}
        metadata["audio"] = audio_meta_raw
    audio_meta = audio_meta_raw
    pause_map_meta = audio_meta.get("pause_map") if isinstance(audio_meta.get("pause_map"), list) else []
    derived_pause_map: List[Dict[str, Any]] = []
    if old_tagged:
        _, derived_pause_map, _ = _parse_tagged_tts(old_tagged)
    effective_pause_map: List[Dict[str, Any]] = list(pause_map_meta) if pause_map_meta else list(derived_pause_map)

    silence_plan: Optional[Sequence[float]] = None
    synthesis_meta = audio_meta.get("synthesis")
    if isinstance(synthesis_meta, dict):
        plan_candidate = synthesis_meta.get("silence_plan")
        if isinstance(plan_candidate, list):
            silence_plan = plan_candidate

    tagged_source: Optional[str] = None
    if old_tagged:
        updated_tagged, tagged_replaced = replace_text(old_tagged, payload.original, payload.replacement, payload.scope)
        if tagged_replaced > 0:
            tagged_source = updated_tagged

    if tagged_source is None:
        tagged_source = _compose_tagged_tts(new_plain, silence_plan, effective_pause_map)

    plain_content, pause_map = _persist_tts_variants(
        base_dir,
        status,
        tagged_source,
        timestamp=timestamp,
        update_assembled=payload.update_assembled,
    )

    save_status(channel_code, video_number, status)

    diff_lines = list(
        difflib.unified_diff(
            old_plain.splitlines(),
            plain_content.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    if len(diff_lines) > 300:
        diff_lines = diff_lines[:300] + ["... (diff truncated)"]

    append_audio_history_entry(
        channel_code,
        video_number,
        {
            "event": "tts_replace",
            "status": "saved",
            "message": "テキスト置換を実行",
            "diff_preview": diff_lines[:20],
        },
    )

    audio_regenerated = False
    message = None
    if payload.regenerate_audio:
        try:
            manager = AudioManager(project_root=PROJECT_ROOT)
            manager.synthesize(channel_code=channel_code, video_number=video_number)
            audio_regenerated = True
            append_audio_history_entry(
                channel_code,
                video_number,
                {
                    "event": "audio_regenerated",
                    "status": "completed",
                    "message": "置換後に音声と字幕を再生成",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Audio regeneration failed for %s/%s", channel_code, video_number)
            message = f"音声の再生成に失敗しました: {exc}"
            append_audio_history_entry(
                channel_code,
                video_number,
                {
                    "event": "audio_regeneration_failed",
                    "status": "failed",
                    "message": str(exc),
                },
            )
    else:
        append_audio_history_entry(
            channel_code,
            video_number,
            {
                "event": "tts_replace",
                "status": "skipped",
                "message": "置換のみ (再生成なし)",
            },
        )

    return TtsReplaceResponse(
        replaced=replaced,
        content=plain_content,
        plain_content=plain_content,
        tagged_content=resolve_text_file(tagged_path),
        pause_map=pause_map or None,
        audio_regenerated=audio_regenerated,
        message=message,
    )


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


@app.get("/api/channels/{channel}/videos", response_model=List[VideoSummaryResponse])
def list_videos(channel: str):
    channel_code = normalize_channel_code(channel)
    planning_rows = {
        normalize_video_number(row.video_number): row for row in planning_store.get_rows(channel_code, force_refresh=True)
        if row.video_number
    }
    video_dirs = list_video_dirs(channel_code)
    video_numbers = set(p.name for p in video_dirs)
    video_numbers.update(planning_rows.keys())
    response: List[VideoSummaryResponse] = []
    for video_number in sorted(video_numbers):
        planning_row = planning_rows.get(video_number)
        character_count: Optional[int] = _character_count_from_a_text(channel_code, video_number)
        try:
            status = load_status(channel_code, video_number)
        except HTTPException as exc:
            if exc.status_code == 404:
                status = None
            else:
                raise
        metadata = status.get("metadata", {}) if status else {}
        stages_dict = status.get("stages", {}) if status else {}
        stages_dict, audio_exists, srt_exists = _inject_audio_completion_from_artifacts(
            channel_code, video_number, stages_dict, metadata
        )
        stages = {key: value.get("status", "pending") for key, value in stages_dict.items()} if stages_dict else {}
        status_value = status.get("status", "unknown") if status else "pending"
        # derive character count from status metadata first
        if character_count is None:
            for key in ("assembled_characters", "output_characters", "chapter_characters"):
                value = metadata.get(key)
                if isinstance(value, (int, float)):
                    character_count = int(value)
                    break
        if planning_row:
            row_raw = planning_row.raw
            # CSV を最新ソースとして統合する
            if row_raw.get("タイトル"):
                metadata["sheet_title"] = row_raw.get("タイトル")
            if row_raw.get("作成フラグ"):
                metadata["sheet_flag"] = row_raw.get("作成フラグ")
            planning_section = get_planning_section(metadata)
            update_planning_from_row(planning_section, row_raw)
            if character_count is None:
                raw_chars = row_raw.get("文字数")
                if isinstance(raw_chars, str) and raw_chars.strip():
                    try:
                        character_count = int(raw_chars.replace(",", ""))
                    except ValueError:
                        character_count = None
        if character_count is None:
            fallback_chars = _fallback_character_count_from_files(metadata, channel_code, video_number)
            if fallback_chars is not None:
                character_count = fallback_chars
        if audio_exists and srt_exists and status_value != "completed":
            status_value = "completed"
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
                ready_for_audio=bool(metadata.get("ready_for_audio", False)),
                stages=stages,
                updated_at=status.get("updated_at") if status else None,
                character_count=character_count,
                planning=planning_payload,
                youtube_description=youtube_description,
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
        is_selected=make_selected,
        created_at=now,
        updated_at=now,
    )


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


@app.post(
    "/api/workspaces/thumbnails/{channel}/library/{asset_name}/describe",
    response_model=ThumbnailDescriptionResponse,
)
def describe_thumbnail_library_asset(channel: str, asset_name: str):
    channel_code = normalize_channel_code(channel)
    _, source_path = _resolve_library_asset_path(channel_code, asset_name)
    text, model_name, provider = _generate_thumbnail_caption(source_path)
    return ThumbnailDescriptionResponse(description=text, model=model_name, source=provider)


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
        return FileResponse(resolved_candidate, media_type=media_type, filename=resolved_candidate.name)

    raise HTTPException(status_code=404, detail="thumbnail asset not found")


@app.get("/thumbnails/library/{channel}/{asset_path:path}")
def get_thumbnail_library_asset(channel: str, asset_path: str):
    channel_code = channel.strip().upper()
    if not channel_code or Path(channel_code).name != channel_code:
        raise HTTPException(status_code=404, detail="invalid channel")
    _, candidate = _resolve_library_asset_path(channel_code, asset_path)
    media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    return FileResponse(candidate, media_type=media_type, filename=candidate.name)


@app.get("/api/channels/{channel}/videos/{video}", response_model=VideoDetailResponse)
def get_video_detail(channel: str, video: str):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    metadata = status.get("metadata", {})
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

    stages_meta = status.get("stages", {}) or {}
    stages_meta, audio_exists, srt_exists = _inject_audio_completion_from_artifacts(channel_code, video_number, stages_meta, metadata)
    stages = {key: value.get("status", "pending") for key, value in stages_meta.items()} if stages_meta else {}
    status_value = status.get("status", "unknown")
    if audio_exists and srt_exists and status_value != "completed":
        status_value = "completed"
    base_dir = video_base_dir(channel_code, video_number)
    content_dir = base_dir / "content"

    assembled_path = content_dir / "assembled.md"
    assembled_human_path = content_dir / "assembled_human.md"
    script_audio_path = content_dir / "script_audio.txt"
    script_audio_human_path = content_dir / "script_audio_human.txt"

    warnings: List[str] = []
    # Bテキスト: audio_prep/b_text_with_pauses.txt を唯一のソースとする。無い場合は警告付きで空にする。
    b_with_pauses = base_dir / "audio_prep" / "b_text_with_pauses.txt"
    if not b_with_pauses.exists():
        warnings.append(f"b_text_with_pauses.txt missing for {channel_code}-{video_number}")
        tts_content = ""
        tts_selected_path = None
    else:
        tts_content = resolve_text_file(b_with_pauses) or ""
        tts_selected_path = b_with_pauses
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

    plain_tts = tts_content
    tagged_path = tts_selected_path
    tagged_tts = resolve_text_file(tagged_path) if tagged_path else None
    if not plain_tts and tagged_tts:
        plain_tts = tagged_tts
    tts_source_path = tts_selected_path if (tts_selected_path and tts_selected_path.exists()) else None

    script_audio_content = resolve_text_file(script_audio_path)
    script_audio_human_content = tts_content

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
    human_b_content = plain_tts  # 最終B（b_text_with_pauses）のみ。無ければ空。

    youtube_description = _build_youtube_description(
        channel_code, video_number, metadata, metadata.get("sheet_title") or metadata.get("title")
    )

    if not audio_path:
        warnings.append(f"audio missing for {channel_code}-{video_number}")
    if not srt_path:
        warnings.append(f"srt missing for {channel_code}-{video_number}")

    return VideoDetailResponse(
        channel=channel_code,
        video=video_number,
        script_id=status.get("script_id") or (planning_row.script_id if planning_row else None),
        title=metadata.get("sheet_title") or metadata.get("title"),
        status=status_value,
        ready_for_audio=bool(metadata.get("ready_for_audio", False)),
        stages=stages,
        redo_script=bool(redo_script),
        redo_audio=bool(redo_audio),
        redo_note=redo_note,
        # A：人間編集版のみ（なければ空）。パスは human があればそれ、無ければ assembled を返す
        assembled_path=safe_relative_path(assembled_human_path) if assembled_human_path.exists() else (safe_relative_path(assembled_path) if assembled_path.exists() else None),
        assembled_content=assembled_content,
        assembled_human_path=None,
        assembled_human_content=None,
        # B：最終成果物の b_text_with_pauses のみ（なければ空）。パスも成果物のみ返す
        tts_path=safe_relative_path(tts_source_path) if tts_source_path else None,
        tts_content=human_b_content,
        tts_plain_content=human_b_content,
        tts_tagged_path=safe_relative_path(tagged_path) if tagged_path and tagged_path.exists() else None,
        tts_tagged_content=tagged_tts,
        # 人間編集版のみを使うため script_audio 系は常に None
        script_audio_path=None,
        script_audio_content=script_audio_content,
        script_audio_human_path=safe_relative_path(tts_source_path) if tts_source_path else None,
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
    )


@app.patch("/api/channels/{channel}/videos/{video}/redo", response_model=RedoUpdateResponse)
def update_video_redo(channel: str, video: str, payload: RedoUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    st = load_status(channel_code, video_number)
    meta = st.metadata or {}

    redo_script = payload.redo_script if payload.redo_script is not None else meta.get("redo_script", True)
    redo_audio = payload.redo_audio if payload.redo_audio is not None else meta.get("redo_audio", True)
    redo_note = payload.redo_note if payload.redo_note is not None else meta.get("redo_note")

    meta["redo_script"] = bool(redo_script)
    meta["redo_audio"] = bool(redo_audio)
    if redo_note is not None:
        meta["redo_note"] = redo_note
    st.metadata = meta
    save_status(st)

    updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return RedoUpdateResponse(
        status="ok",
        redo_script=bool(redo_script),
        redo_audio=bool(redo_audio),
        redo_note=redo_note,
        updated_at=updated_at,
    )


@app.patch("/api/channels/{channel}/videos/{video}/thumbnail", response_model=ThumbnailOverrideResponse)
def update_video_thumbnail_override(channel: str, video: str, payload: ThumbnailOverrideRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    st = load_status(channel_code, video_number)
    meta = st.metadata or {}

    meta["thumbnail_url_override"] = payload.thumbnail_url
    if payload.thumbnail_path is not None:
        meta["thumbnail_path_override"] = payload.thumbnail_path

    st.metadata = meta
    save_status(st)

    updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return ThumbnailOverrideResponse(
        status="ok",
        thumbnail_url=payload.thumbnail_url,
        thumbnail_path=payload.thumbnail_path,
        updated_at=updated_at,
    )


def _clear_redo_flags(channel: str, video: str, *, redo_script: Optional[bool] = None, redo_audio: Optional[bool] = None):
    """
    ベストエフォートでリテイクフラグを更新する（API内部利用）。音声成功時は redo_audio=False、台本保存時は redo_script=False などに利用。
    """
    try:
        channel_code = normalize_channel_code(channel)
        video_number = normalize_video_number(video)
        st = load_status(channel_code, video_number)
        meta = st.metadata or {}
        if redo_script is not None:
            meta["redo_script"] = bool(redo_script)
        if redo_audio is not None:
            meta["redo_audio"] = bool(redo_audio)
        st.metadata = meta
        save_status(st)
    except Exception:
        # ベストエフォートなので握りつぶす
        pass


def _find_thumbnails(channel: str, video: Optional[str] = None, title: Optional[str] = None, limit: int = 3) -> List[Dict[str, str]]:
    """
    thumbnails/ 配下からチャンネルコード・動画番号に合致しそうなサムネをスコアで探す。
    スコア: channel一致 +3, video番号含む(+2) / 数字一致(+2)、タイトルワード一致(+1)。スコア同点は更新日時降順。
    """
    base = PROJECT_ROOT / "thumbnails"
    if not base.exists():
        return []
    channel_code = normalize_channel_code(channel)
    video_no = normalize_video_number(video) if video else None
    video_no_int = None
    if video_no and video_no.isdigit():
        try:
            video_no_int = int(video_no)
        except Exception:
            video_no_int = None
    title_tokens: List[str] = []
    if title:
        # 短い単語のみ加点対象
        title_tokens = [t.lower() for t in re.findall(r"[\\w一-龠ぁ-んァ-ヴー]+", title) if len(t) >= 2]
    matches: List[Tuple[int, float, Path]] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in [".png", ".jpg", ".jpeg", ".webp"]:
            continue
        score = 0
        lower = str(path).lower()
        if channel_code.lower() in lower:
            score += 3

        video_matched = False
        if video_no and video_no in lower:
            score += 2
            video_matched = True
        elif video_no_int is not None:
            nums = re.findall(r"(\\d{1,4})", lower)
            for n in nums:
                try:
                    if int(n) == video_no_int:
                        score += 2
                        video_matched = True
                        break
                except Exception:
                    continue
        if title_tokens:
            for tok in title_tokens:
                if tok and tok in lower:
                    score += 1
                    break
        if video_no and not video_matched:
            continue
        if score == 0 and channel_code.lower() not in lower:
            continue
        mtime = path.stat().st_mtime
        matches.append((score, mtime, path))
    matches.sort(key=lambda x: (-x[0], -x[1]))
    results: List[Dict[str, str]] = []
    for _, _, p in matches[:limit]:
        rel = p.relative_to(PROJECT_ROOT)
        url = f"/{rel.as_posix()}"
        results.append({"path": str(rel), "url": url, "name": p.name})
    return results


@app.get("/api/redo", response_model=List[RedoItemResponse])
def list_redo_items(
    channel: Optional[str] = Query(None, description="CHコード (例: CH02)"),
    type: Optional[str] = Query(None, description="script|audio|all で絞り込み"),
):
    channel_filter = normalize_channel_code(channel) if channel else None
    type_filter = (type or "").lower()
    want_script = type_filter in ("script", "all", "") or type_filter not in ("audio", "script")
    want_audio = type_filter in ("audio", "all", "") or type_filter not in ("audio", "script")

    results: List[RedoItemResponse] = []
    for ch_dir in list_channel_dirs():
        ch_code = ch_dir.name.upper()
        if channel_filter and ch_code != channel_filter:
            continue
        for vid_dir in list_video_dirs(ch_code):
            video_number = vid_dir.name
            try:
                st = load_status(ch_code, video_number)
            except HTTPException:
                continue
            meta = st.get("metadata", {}) if isinstance(st.get("metadata", {}), dict) else {}
            redo_script = meta.get("redo_script")
            redo_audio = meta.get("redo_audio")
            redo_note = meta.get("redo_note")
            if redo_script is None:
                redo_script = True
            if redo_audio is None:
                redo_audio = True
            # type filter
            if type_filter in ("script", "audio"):
                if type_filter == "script" and not redo_script:
                    continue
                if type_filter == "audio" and not redo_audio:
                    continue
            if not want_script and not want_audio:
                continue
            title = meta.get("sheet_title") or meta.get("title")
            results.append(
                RedoItemResponse(
                    channel=ch_code,
                    video=video_number,
                    redo_script=bool(redo_script),
                    redo_audio=bool(redo_audio),
                    redo_note=redo_note,
                    title=title,
                    status=st.get("status"),
                )
            )
    return results


@app.get("/api/redo/summary", response_model=List[RedoSummaryItem])
def list_redo_summary(
    channel: Optional[str] = Query(None, description="CHコード (例: CH02)"),
):
    channel_filter = normalize_channel_code(channel) if channel else None
    summaries: Dict[str, Dict[str, int]] = {}

    for ch_dir in list_channel_dirs():
        ch_code = ch_dir.name.upper()
        if channel_filter and ch_code != channel_filter:
            continue
        sums = summaries.setdefault(ch_code, {"redo_script": 0, "redo_audio": 0, "redo_both": 0})
        for vid_dir in list_video_dirs(ch_code):
            st_path = vid_dir / "status.json"
            if not st_path.exists():
                continue
            try:
                st = load_status(ch_code, vid_dir.name)
                meta = st.get("metadata", {}) if isinstance(st.get("metadata", {}), dict) else {}
                redo_script = meta.get("redo_script")
                redo_audio = meta.get("redo_audio")
                if redo_script is None:
                    redo_script = True
                if redo_audio is None:
                    redo_audio = True
                if redo_script:
                    sums["redo_script"] += 1
                if redo_audio:
                    sums["redo_audio"] += 1
                if redo_script and redo_audio:
                    sums["redo_both"] += 1
            except Exception:
                continue

    return [
        RedoSummaryItem(
            channel=ch,
            redo_script=vals["redo_script"],
            redo_audio=vals["redo_audio"],
            redo_both=vals["redo_both"],
        )
        for ch, vals in sorted(summaries.items())
    ]


@app.get("/api/channels/{channel}/videos/{video}/tts/plain", response_model=ScriptTextResponse)
def get_tts_plain_text(channel: str, video: str):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    base_dir = video_base_dir(channel_code, video_number)
    tts_path = base_dir / "audio_prep" / "script_sanitized.txt"
    if not tts_path.exists():
        raise HTTPException(status_code=404, detail="script_sanitized.txt not found")
    content = resolve_text_file(tts_path) or ""
    updated_at = None
    try:
        updated_at = (
            datetime.fromtimestamp(tts_path.stat().st_mtime, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except OSError:
        updated_at = None
    return ScriptTextResponse(
        path=safe_relative_path(tts_path),
        content=content,
        updated_at=updated_at,
    )


@app.put("/api/channels/{channel}/videos/{video}/assembled")
def update_assembled(channel: str, video: str, payload: TextUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    base_dir = video_base_dir(channel_code, video_number)
    path = base_dir / "content" / "assembled.md"
    if path.parent.name != "content":
        raise HTTPException(status_code=400, detail="invalid assembled path")
    write_text_with_lock(path, payload.content)
    timestamp = current_timestamp()
    status["updated_at"] = timestamp
    # 台本リテイクは保存成功時に自動解除（ベストエフォート）
    meta = status.get("metadata") or {}
    meta["redo_script"] = False
    status["metadata"] = meta
    save_status(channel_code, video_number, status)
    return {"status": "ok", "updated_at": timestamp}


@app.get("/api/channels/{channel}/videos/{video}/scripts/human", response_model=HumanScriptResponse)
def get_human_scripts(channel: str, video: str):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    metadata = status.get("metadata") or {}
    base_dir = video_base_dir(channel_code, video_number)
    content_dir = base_dir / "content"

    assembled = content_dir / "assembled.md"
    assembled_human = content_dir / "assembled_human.md"
    script_audio = content_dir / "script_audio.txt"
    script_audio_human = content_dir / "script_audio_human.txt"
    warnings: List[str] = []
    b_with_pauses = base_dir / "audio_prep" / "b_text_with_pauses.txt"
    if not b_with_pauses.exists():
        warnings.append(f"b_text_with_pauses.txt missing for {channel_code}-{video_number}")
    plain_tts = resolve_text_file(b_with_pauses) or ""

    assembled_content = resolve_text_file(assembled) or ""
    if not assembled_content:
        assembled_content = plain_tts
    assembled_human_content = resolve_text_file(assembled_human) or ""
    if not assembled_human_content:
        assembled_human_content = assembled_content

    return HumanScriptResponse(
        assembled_path=safe_relative_path(assembled) if assembled.exists() else None,
        assembled_content=assembled_content,
        assembled_human_path=safe_relative_path(assembled_human) if assembled_human.exists() else None,
        assembled_human_content=assembled_human_content,
        script_audio_path=safe_relative_path(script_audio) if script_audio.exists() else None,
        script_audio_content=resolve_text_file(script_audio),
        script_audio_human_path=safe_relative_path(b_with_pauses) if b_with_pauses.exists() else None,
        # 人間編集版のBテキストとして b_text_with_pauses を返す
        script_audio_human_content=plain_tts,
        audio_reviewed=bool(metadata.get("audio_reviewed", False)),
        updated_at=status.get("updated_at"),
        warnings=warnings,
    )


@app.put("/api/channels/{channel}/videos/{video}/scripts/human")
def update_human_scripts(channel: str, video: str, payload: HumanScriptUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    base_dir = video_base_dir(channel_code, video_number)
    content_dir = base_dir / "content"
    audio_prep_dir = base_dir / "audio_prep"

    timestamp = current_timestamp()

    if payload.assembled_human is not None:
        target = content_dir / "assembled_human.md"
        if target.parent.name != "content":
            raise HTTPException(status_code=400, detail="invalid assembled_human path")
        write_text_with_lock(target, payload.assembled_human)
    if payload.script_audio_human is not None:
        target = content_dir / "script_audio_human.txt"
        if target.parent.name != "content":
            raise HTTPException(status_code=400, detail="invalid script_audio_human path")
        write_text_with_lock(target, payload.script_audio_human)
        audio_prep_dir.mkdir(parents=True, exist_ok=True)
        write_text_with_lock(audio_prep_dir / "b_text_with_pauses.txt", payload.script_audio_human)
    if payload.audio_reviewed is not None:
        metadata = status.setdefault("metadata", {})
        metadata["audio_reviewed"] = bool(payload.audio_reviewed)

    status["updated_at"] = timestamp
    save_status(channel_code, video_number, status)
    return {"status": "ok", "updated_at": timestamp, "audio_reviewed": status.get("metadata", {}).get("audio_reviewed", False)}


@app.put("/api/channels/{channel}/videos/{video}/tts")
def update_tts(channel: str, video: str, payload: TtsUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    base_dir = video_base_dir(channel_code, video_number)
    plain_path = base_dir / "audio_prep" / "script_sanitized.txt"
    tagged_path = base_dir / "audio_prep" / "script_sanitized_with_pauses.txt"

    old_plain = resolve_text_file(plain_path) or ""
    old_tagged = resolve_text_file(tagged_path)

    metadata = status.setdefault("metadata", {})
    audio_meta_raw = metadata.get("audio")
    if not isinstance(audio_meta_raw, dict):
        audio_meta_raw = {}
        metadata["audio"] = audio_meta_raw
    audio_meta = audio_meta_raw
    pause_map_meta = audio_meta.get("pause_map") if isinstance(audio_meta.get("pause_map"), list) else []
    derived_pause_map: List[Dict[str, Any]] = []
    if old_tagged:
        _, derived_pause_map, _ = _parse_tagged_tts(old_tagged)
    effective_pause_map: List[Dict[str, Any]] = list(pause_map_meta) if pause_map_meta else list(derived_pause_map)

    silence_plan: Optional[Sequence[float]] = None
    synthesis_meta = audio_meta.get("synthesis")
    if isinstance(synthesis_meta, dict):
        plan_candidate = synthesis_meta.get("silence_plan")
        if isinstance(plan_candidate, list):
            silence_plan = plan_candidate

    if payload.tagged_content is not None:
        tagged_source = payload.tagged_content
    else:
        if payload.content is None:
            raise HTTPException(status_code=400, detail="content または tagged_content を指定してください。")
        tagged_source = _compose_tagged_tts(payload.content, silence_plan, effective_pause_map)

    timestamp = current_timestamp()
    status["updated_at"] = timestamp

    plain_content, pause_map = _persist_tts_variants(
        base_dir,
        status,
        tagged_source,
        timestamp=timestamp,
        update_assembled=payload.update_assembled or False,
    )

    save_status(channel_code, video_number, status)

    diff_lines = list(
        difflib.unified_diff(
            old_plain.splitlines(),
            plain_content.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    if len(diff_lines) > 300:
        diff_lines = diff_lines[:300] + ["... (diff truncated)"]

    initial_message = "音声用テキストを保存" + (" (再生成あり)" if payload.regenerate_audio else "")
    append_audio_history_entry(
        channel_code,
        video_number,
        {
            "event": "tts_saved",
            "status": "saved",
            "message": initial_message,
            "diff_preview": diff_lines[:20],
        },
    )

    audio_regenerated = False
    message = None
    if payload.regenerate_audio:
        manager = AudioManager(project_root=PROJECT_ROOT)
        try:
            manager.synthesize(channel_code=channel_code, video_number=video_number)
            audio_regenerated = True
            message = "音声と字幕を再生成しました。"
            append_audio_history_entry(
                channel_code,
                video_number,
                {
                    "event": "audio_regenerated",
                    "status": "completed",
                    "message": message,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Audio regeneration failed for %s/%s", channel_code, video_number)
            message = f"音声再生成に失敗しました: {exc}"
            append_audio_history_entry(
                channel_code,
                video_number,
                {
                    "event": "audio_regeneration_failed",
                    "status": "failed",
                    "message": str(exc),
                },
            )

    response: Dict[str, Any] = {
        "status": "ok",
        "updated_at": timestamp,
        "diff": diff_lines,
        "audio_regenerated": audio_regenerated,
        "plain_content": plain_content,
        "tagged_content": resolve_text_file(tagged_path),
        "pause_map": pause_map or None,
    }
    if message:
        response["message"] = message
    return response


@app.post(
    "/api/channels/{channel}/videos/{video}/tts/validate",
    response_model=TTSValidateResponse,
)
def validate_tts(channel: str, video: str, payload: TTSValidateRequest):
    sanitized, issues = analyze_tts_content(payload.content)
    return TTSValidateResponse(
        sanitized_content=sanitized,
        issues=issues,
        valid=len(issues) == 0,
    )


@app.put("/api/channels/{channel}/videos/{video}/srt")
def update_srt(channel: str, video: str, payload: TextUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    base_dir = video_base_dir(channel_code, video_number)
    srt_path = resolve_srt_path(status, base_dir)
    if not srt_path:
        raise HTTPException(status_code=404, detail="SRT file not found")
    write_text_with_lock(srt_path, payload.content)
    timestamp = current_timestamp()
    status["updated_at"] = timestamp
    metadata = status.setdefault("metadata", {})
    audio_meta = metadata.setdefault("audio", {})
    synthesis_meta = audio_meta.setdefault("synthesis", {})
    synthesis_meta["final_srt"] = safe_relative_path(srt_path) or str(srt_path)
    synthesis_meta["updated_at"] = timestamp
    save_status(channel_code, video_number, status)
    return {"status": "ok", "updated_at": timestamp}


@app.post(
    "/api/channels/{channel}/videos/{video}/srt/verify",
    response_model=SRTVerifyResponse,
)
def verify_srt(
    channel: str,
    video: str,
    tolerance_ms: int = Query(50, ge=0, le=2000),
):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    base_dir = video_base_dir(channel_code, video_number)
    wav_path = resolve_audio_path(status, base_dir)
    if not wav_path:
        raise HTTPException(
            status_code=404,
            detail="音声ファイルが見つかりません。Stage10 を完了してください。",
        )
    srt_path = resolve_srt_path(status, base_dir)
    if not srt_path:
        raise HTTPException(
            status_code=404,
            detail="SRT ファイルが見つかりません。Stage11 を確認してください。",
        )
    if not wav_path.exists():
        raise HTTPException(status_code=404, detail=f"WAV not found: {wav_path}")
    if not srt_path.exists():
        raise HTTPException(status_code=404, detail=f"SRT not found: {srt_path}")
    return verify_srt_file(wav_path, srt_path, tolerance_ms=tolerance_ms)


@app.put("/api/channels/{channel}/videos/{video}/status")
def update_status(channel: str, video: str, payload: StatusUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    timestamp = current_timestamp()
    status["status"] = payload.status
    status["updated_at"] = timestamp
    if payload.status.lower() == "completed":
        status.setdefault("completed_at", timestamp)
    else:
        status.pop("completed_at", None)
    save_status(channel_code, video_number, status)
    return {"status": "ok", "updated_at": timestamp}


@app.put("/api/channels/{channel}/videos/{video}/stages")
def update_stages(channel: str, video: str, payload: StageUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    timestamp = current_timestamp()
    stages = status.setdefault("stages", {})
    for key, value in payload.stages.items():
        stage_entry = stages.setdefault(key, {})
        stage_entry["status"] = value.status
        stage_entry["updated_at"] = timestamp
    status["updated_at"] = timestamp
    save_status(channel_code, video_number, status)
    return {"status": "ok", "updated_at": timestamp}


@app.put("/api/channels/{channel}/videos/{video}/ready")
def update_ready(channel: str, video: str, payload: ReadyUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    now_iso = current_timestamp()
    metadata = status.setdefault("metadata", {})
    metadata["ready_for_audio"] = payload.ready
    if payload.ready:
        metadata["ready_for_audio_at"] = current_timestamp_compact()
    else:
        metadata.pop("ready_for_audio_at", None)
    status["updated_at"] = now_iso
    save_status(channel_code, video_number, status)
    return {"status": "ok", "updated_at": now_iso}


@app.put(
    "/api/channels/{channel}/videos/{video}/planning",
    response_model=PlanningUpdateResponse,
)
def update_planning(channel: str, video: str, payload: PlanningUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    metadata = status.setdefault("metadata", {})
    planning_section = get_planning_section(metadata)

    changed = False
    for key, raw_value in payload.fields.items():
        normalized_value = normalize_optional_text(raw_value)
        current_value = normalize_optional_text(planning_section.get(key))
        if normalized_value == current_value:
            continue
        changed = True
        if normalized_value is None:
            planning_section.pop(key, None)
        else:
            planning_section[key] = normalized_value

    if payload.creation_flag is not None:
        normalized_flag = normalize_optional_text(payload.creation_flag)
        existing_flag = normalize_optional_text(metadata.get("sheet_flag"))
        if normalized_flag != existing_flag:
            changed = True
            if normalized_flag is None:
                metadata.pop("sheet_flag", None)
                metadata.pop("blocked_by_sheet", None)
            else:
                metadata["sheet_flag"] = normalized_flag
                if normalized_flag in {"2", "9"}:
                    metadata["blocked_by_sheet"] = True
                else:
                    metadata.pop("blocked_by_sheet", None)

    planning_payload = build_planning_payload(metadata)

    if not changed:
        return PlanningUpdateResponse(
            status="noop",
            updated_at=status.get("updated_at") or "",
            planning=planning_payload,
        )

    timestamp = current_timestamp()
    status["updated_at"] = timestamp
    save_status(channel_code, video_number, status)
    run_ssot_sync_for_channel(channel_code, video_number)
    planning_payload = build_planning_payload(metadata)
    return PlanningUpdateResponse(status="ok", updated_at=timestamp, planning=planning_payload)


def _task_response(task_id: str, record: Dict[str, Any]) -> BatchWorkflowTaskResponse:
    return BatchWorkflowTaskResponse(
        task_id=task_id,
        channel_code=record.get("channel") or record.get("channel_code") or "",
        video_numbers=record.get("videos") or record.get("video_numbers") or [],
        status=record.get("status", BatchTaskStatus.pending.value),
        log_path=record.get("log_path"),
        config_path=record.get("config_path"),
        created_at=record.get("created_at"),
        queue_entry_id=record.get("queue_id"),
    )


@app.post("/api/batch-workflow/start", response_model=BatchWorkflowTaskResponse)
async def start_batch_workflow(payload: BatchWorkflowRequest):
    channel_code = normalize_channel_code(payload.channel_code)
    video_numbers = [normalize_video_number(v) for v in payload.video_numbers]
    if _channel_is_busy(channel_code):
        raise HTTPException(
            status_code=409,
            detail=f"{channel_code} は別のバッチを実行中です。キューが完了するまでお待ちください。",
        )
    task_id = _launch_batch_task(channel_code, video_numbers, payload.config)
    return _task_response(task_id, TASK_REGISTRY[task_id])


@app.post("/api/batch-workflow/queue", response_model=BatchQueueEntryResponse)
async def enqueue_batch_workflow(payload: BatchQueueRequest):
    channel_code = normalize_channel_code(payload.channel_code)
    video_numbers = [normalize_video_number(v) for v in payload.video_numbers]
    entry_id = insert_queue_entry(channel_code, video_numbers, payload.config)
    entry = get_queue_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=500, detail="キュー登録に失敗しました。")
    asyncio.create_task(_maybe_start_queue(channel_code))
    return _build_queue_response(entry)


@app.get("/api/batch-workflow/queue", response_model=List[BatchQueueEntryResponse])
def list_batch_queue(channel: Optional[str] = Query(None)):
    entries = list_queue_entries(channel)
    return [_build_queue_response(entry) for entry in entries]


@app.post("/api/batch-workflow/queue/{entry_id}/cancel", response_model=BatchQueueEntryResponse)
def cancel_queue_entry(entry_id: int):
    entry = get_queue_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="queue entry not found")
    if entry["status"] == QueueEntryStatus.running.value:
        # running でもキャンセルを許可し、バックグラウンドタスクを停止
        cancel_background_task(entry.get("task_id"))
        update_queue_entry_status(entry_id, QueueEntryStatus.cancelled)
        _update_queue_progress(entry_id, status=QueueEntryStatus.cancelled.value, current_video=None)
        entry = get_queue_entry(entry_id)
        if entry:
            return _build_queue_response(entry)
        raise HTTPException(status_code=500, detail="queue entry not found after cancel")
    if entry["status"] in {
        QueueEntryStatus.cancelled.value,
        QueueEntryStatus.succeeded.value,
        QueueEntryStatus.failed.value,
    }:
        return _build_queue_response(entry)
    update_queue_entry_status(entry_id, QueueEntryStatus.cancelled)
    _update_queue_progress(entry_id, status=QueueEntryStatus.cancelled.value, current_video=None)
    entry = get_queue_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="queue entry not found")
    return _build_queue_response(entry)


@app.get("/api/batch-workflow/{task_id}", response_model=BatchWorkflowTaskResponse)
def get_batch_workflow(task_id: str):
    record = TASK_REGISTRY.get(task_id)
    if record:
        return _task_response(task_id, {
            "channel": record["channel_code"],
            "videos": record["video_numbers"],
            "status": record["status"],
            "log_path": record.get("log_path"),
            "config_path": record.get("config_path"),
            "created_at": record.get("created_at"),
        })
    db_record = load_task_record(task_id)
    if not db_record:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_response(task_id, db_record)


@app.get("/api/batch-workflow/{task_id}/log", response_model=BatchWorkflowLogResponse)
def get_batch_workflow_log(task_id: str, tail: int = Query(200, ge=1, le=2000)):
    record = TASK_REGISTRY.get(task_id) or load_task_record(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="task not found")
    log_path = Path(record.get("log_path") or "")
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="log file not found")
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return BatchWorkflowLogResponse(task_id=task_id, lines=lines[-tail:])


@app.get("/api/channels/{channel}/videos/{video}/audio")
def get_audio(channel: str, video: str):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    audio_path = resolve_audio_path(status, video_base_dir(channel_code, video_number))
    if not audio_path:
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(audio_path, media_type="audio/wav", filename=audio_path.name)


@app.get("/api/channels/{channel}/videos/{video}/srt")
def get_srt(channel: str, video: str):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    srt_path = resolve_srt_path(status, video_base_dir(channel_code, video_number))
    if not srt_path:
        raise HTTPException(status_code=404, detail="SRT not found")
    return FileResponse(srt_path, media_type="text/plain", filename=srt_path.name)


@app.get("/api/channels/{channel}/videos/{video}/log")
def get_audio_log(channel: str, video: str):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    log_path = resolve_log_path(status, video_base_dir(channel_code, video_number))
    if not log_path:
        raise HTTPException(status_code=404, detail="Log not found")
    return FileResponse(log_path, media_type="application/json", filename=log_path.name)


@app.get("/api/audio-tts-v2/health")
def audio_tts_v2_health():
    try:
        cfg = load_routing_config()
    except Exception as exc:
        return {"status": "error", "detail": f"routing_config_load_failed: {exc}"}

    result = {
        "status": "ok",
        "engine_default": getattr(cfg, "engine_default", None),
        "engine_override_env": os.getenv("ENGINE_DEFAULT_OVERRIDE"),
        "voicevox": {
            "url": getattr(cfg, "voicevox_url", None),
            "speaker_env": getattr(cfg, "voicevox_speaker_env", None),
            "ok": False,
            "detail": None,
        },
        "azure_openai": {
            "api_key_present": bool(os.getenv("AZURE_OPENAI_API_KEY")),
            "endpoint": os.getenv("AZURE_OPENAI_ENDPOINT"),
            "deployment": os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        },
        "elevenlabs": {
            "api_key_present": bool(os.getenv(getattr(cfg, "eleven_api_key_env", ""))),
            "voice_id": resolve_eleven_voice("", cfg=cfg) if getattr(cfg, "eleven_voice_id", None) else None,
            "model_id": resolve_eleven_model(cfg) if cfg else None,
        },
    }
    # Voicevox ping (best effort)
    try:
        if getattr(cfg, "voicevox_url", None):
            resp = requests.get(f"{cfg.voicevox_url}/speakers", timeout=2)
            resp.raise_for_status()
            result["voicevox"]["ok"] = True
    except Exception as exc:  # pragma: no cover - best effort check
        result["voicevox"]["detail"] = str(exc)
    return result


@app.get("/api/audio/integrity", response_model=List[AudioIntegrityItem])
def audio_integrity_report():
    items: List[AudioIntegrityItem] = []
    for channel, video, video_dir in _iter_video_dirs():
        base_dir = video_dir
        audio_prep = base_dir / "audio_prep"
        b_path = audio_prep / "b_text_with_pauses.txt"
        wav_path = audio_prep / f"{channel}-{video}.wav"
        srt_path = audio_prep / f"{channel}-{video}.srt"
        missing: List[str] = []
        if not b_path.exists():
            missing.append("b_text_with_pauses.txt")
        if not wav_path.exists():
            missing.append(f"{channel}-{video}.wav")
        if not srt_path.exists():
            missing.append(f"{channel}-{video}.srt")
        audio_duration = get_audio_duration_seconds(wav_path) if wav_path.exists() else None
        srt_duration = _infer_srt_duration_seconds(srt_path) if srt_path.exists() else None
        duration_diff = None
        if audio_duration is not None and srt_duration is not None:
            duration_diff = abs(audio_duration - srt_duration)
            if duration_diff < 0.01:
                duration_diff = 0.0
        items.append(
            AudioIntegrityItem(
                channel=channel,
                video=video,
                missing=missing,
                audio_path=safe_relative_path(wav_path) if wav_path.exists() else None,
                srt_path=safe_relative_path(srt_path) if srt_path.exists() else None,
                b_text_path=safe_relative_path(b_path) if b_path.exists() else None,
                audio_duration=audio_duration,
                srt_duration=srt_duration,
                duration_diff=duration_diff,
            )
        )
    return items


@app.get("/api/audio/analysis/{channel}/{video}", response_model=AudioAnalysisResponse)
def audio_analysis(channel: str, video: str):
    return _load_audio_analysis(channel, video)


def _resolve_script_pipeline_input_path(channel: str, video: str) -> Path:
    """
    旧式の解決（後方互換）。呼び出し元は _resolve_final_tts_input_path を優先すること。
    """
    base = PROJECT_ROOT / "script_pipeline" / "data" / channel / video
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
    音声生成で必ず参照する最終確定入力を解決する。
    優先度（人手が介入した最新版を最優先）:
    1) audio_prep/script_audio_human.txt
    2) content/script_audio_human.txt
    3) content/assembled_human.md
    4) audio_prep/script_sanitized.txt
    5) content/script_audio.txt
    6) content/assembled.md
    見つからない場合は 404 を返す。
    """
    base = PROJECT_ROOT / "script_pipeline" / "data" / channel / video
    candidates = [
        base / "audio_prep" / "script_audio_human.txt",
        base / "content" / "script_audio_human.txt",
        base / "content" / "assembled_human.md",
        base / "audio_prep" / "script_sanitized.txt",
        base / "content" / "script_audio.txt",
        base / "content" / "assembled.md",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise HTTPException(status_code=404, detail=f"final tts input not found: {channel}-{video}")


def _resolve_a_text_display_path(channel: str, video: str) -> Path:
    """
    Aテキスト（表示用）用に解決するパス。
    優先: content/assembled_human.md -> content/assembled.md -> audio_prep/script_sanitized.txt
    """
    base = PROJECT_ROOT / "script_pipeline" / "data" / channel / video
    candidates = [
        base / "content" / "assembled_human.md",
        base / "content" / "assembled.md",
        base / "audio_prep" / "script_sanitized.txt",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise HTTPException(status_code=404, detail=f"A-text not found: {channel}-{video}")


@app.get("/api/channels/{channel}/videos/{video}/a-text", response_class=PlainTextResponse)
def api_get_a_text(channel: str, video: str):
    """
    Aテキスト（表示用原稿）を返す。優先順位:
    content/assembled_human.md -> content/assembled.md -> audio_prep/script_sanitized.txt
    """
    channel_code = normalize_channel_code(channel)
    video_no = normalize_video_number(video)
    path = _resolve_a_text_display_path(channel_code, video_no)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="A-text not found")
    return text


@app.post("/api/audio-tts-v2/run-from-script")
def api_audio_tts_v2_run_from_script(
    channel: str = Body(..., embed=True),
    video: str = Body(..., embed=True),
    engine_override: Optional[str] = Body(None),
    reading_source: Optional[str] = Body(None),
):
    channel_code = normalize_channel_code(channel)
    video_no = normalize_video_number(video)
    input_path = _resolve_final_tts_input_path(channel_code, video_no)
    payload = TtsV2Request(
        channel=channel_code,
        video=video_no,
        input_path=str(input_path),
        engine_override=engine_override,
        reading_source=reading_source,
    )
    return _run_audio_tts_v2(payload)


# === audio_tts_v2 integration (simple CLI bridge) ===
class TtsV2Request(BaseModel):
    channel: str
    video: str
    input_path: str
    engine_override: Optional[str] = Field(None, description="voicevox|voicepeak|elevenlabs を強制する場合")
    reading_source: Optional[str] = Field(None, description="voicepeak用読み取得ソース")
    voicepeak_narrator: Optional[str] = None
    voicepeak_speed: Optional[int] = None
    voicepeak_pitch: Optional[int] = None
    voicepeak_emotion: Optional[str] = None


def _run_audio_tts_v2(req: TtsV2Request) -> Dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "audio_tts_v2" / "scripts" / "run_tts.py"
    if not script.exists():
        raise HTTPException(status_code=500, detail="run_tts.py not found")
    input_path = Path(req.input_path)
    if not input_path.is_absolute():
        input_path = (repo_root / input_path).resolve()
    if not input_path.exists():
        raise HTTPException(status_code=400, detail=f"input_path not found: {input_path}")
    env = os.environ.copy()
    # audio_tts_v2 のモジュールを優先するため既存PYTHONPATHを上書き
    env["PYTHONPATH"] = str(repo_root / "audio_tts_v2")
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

    # Always write to artifacts/final so downstream CapCut uses latest audio/SRT.
    final_dir = repo_root / "audio_tts_v2" / "artifacts" / "final" / req.channel / req.video
    final_dir.mkdir(parents=True, exist_ok=True)
    final_wav_path = final_dir / f"{req.channel}-{req.video}.wav"
    final_log_path = final_dir / "log.json"
    cmd.extend(["--out-wav", str(final_wav_path), "--log", str(final_log_path)])
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
        raise HTTPException(status_code=500, detail=f"audio_tts_v2 failed: {e.stderr or e.stdout or e}")
    stdout = completed.stdout.strip()
    if not final_wav_path.exists():
        raise HTTPException(status_code=500, detail=f"audio_tts_v2 did not create wav: {stdout}")
    final_srt_path = final_wav_path.with_suffix(".srt")
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


@app.post("/api/audio-tts-v2/run")
def api_audio_tts_v2_run(payload: TtsV2Request):
    channel_code = normalize_channel_code(payload.channel)
    video_no = normalize_video_number(payload.video)
    resolved = _resolve_final_tts_input_path(channel_code, video_no)

    provided = Path(payload.input_path)
    repo_root = Path(__file__).resolve().parents[2]
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
    return _run_audio_tts_v2(fixed)


class TtsV2BatchItem(BaseModel):
    channel: str
    video: str
    input_path: str
    engine_override: Optional[str] = None
    reading_source: Optional[str] = None
    voicepeak_narrator: Optional[str] = None
    voicepeak_speed: Optional[int] = None
    voicepeak_pitch: Optional[int] = None
    voicepeak_emotion: Optional[str] = None


class TtsV2BatchResponse(BaseModel):
    results: List[Dict[str, Any]]
    success_count: int
    failure_count: int


@app.post("/api/audio-tts-v2/run-batch", response_model=TtsV2BatchResponse)
def api_audio_tts_v2_run_batch(payload: List[TtsV2BatchItem]):
    results: List[Dict[str, Any]] = []
    success = 0
    failure = 0
    for item in payload:
        try:
            channel_code = normalize_channel_code(item.channel)
            video_no = normalize_video_number(item.video)
            resolved = _resolve_final_tts_input_path(channel_code, video_no)
            provided = Path(item.input_path)
            repo_root = Path(__file__).resolve().parents[2]
            if not provided.is_absolute():
                provided = (repo_root / provided).resolve()
            if provided.resolve() != resolved.resolve():
                raise HTTPException(
                    status_code=400,
                    detail=f"input_path must be final script: {resolved} (provided: {provided})",
                )
            res = _run_audio_tts_v2(
                TtsV2Request(
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
    return TtsV2BatchResponse(results=results, success_count=success, failure_count=failure)


class BatchTtsProgressResponse(BaseModel):
    status: str  # idle, running, completed, error
    current_channel: Optional[str] = None
    current_video: Optional[str] = None
    completed: int = 0
    total: int = 0
    success: int = 0
    failed: int = 0
    current_step: Optional[str] = None
    errors: List[Dict[str, Any]] = []
    updated_at: Optional[str] = None
    channels: Optional[Dict[str, Any]] = None


@app.get("/api/batch-tts/progress", response_model=BatchTtsProgressResponse)
def get_batch_tts_progress():
    """バッチTTS再生成の進捗を取得"""
    progress_file = PROJECT_ROOT / "batch_tts_progress.json"
    if not progress_file.exists():
        return BatchTtsProgressResponse(
            status="idle",
            completed=0,
            total=0,
            success=0,
            failed=0,
            channels={
                "CH06": {"total": 33, "completed": 0, "success": 0, "failed": 0},
                "CH02": {"total": 82, "completed": 0, "success": 0, "failed": 0},
                "CH04": {"total": 30, "completed": 0, "success": 0, "failed": 0},
            },
        )
    try:
        data = json.loads(progress_file.read_text(encoding="utf-8"))
        return BatchTtsProgressResponse(**data)
    except Exception as exc:
        logger.warning("Failed to read batch_tts_progress.json: %s", exc)
        return BatchTtsProgressResponse(status="error", current_step=str(exc))


@app.post("/api/batch-tts/start")
async def start_batch_tts_regeneration(
    channels: List[str] = Body(default=["CH06", "CH02", "CH04"]),
    background_tasks: BackgroundTasks = None,
):
    """バッチTTS再生成をバックグラウンドで開始"""
    progress_file = PROJECT_ROOT / "batch_tts_progress.json"
    # 既に実行中かチェック
    if progress_file.exists():
        try:
            data = json.loads(progress_file.read_text(encoding="utf-8"))
            if data.get("status") == "running":
                raise HTTPException(status_code=409, detail="バッチTTS再生成は既に実行中です。")
        except json.JSONDecodeError:
            pass
    
    # 進捗初期化
    initial_progress = {
        "status": "running",
        "current_channel": None,
        "current_video": None,
        "completed": 0,
        "total": 145,
        "success": 0,
        "failed": 0,
        "current_step": "開始中...",
        "errors": [],
        "updated_at": datetime.now().isoformat(),
        "channels": {
            "CH06": {"total": 33, "completed": 0, "success": 0, "failed": 0},
            "CH02": {"total": 82, "completed": 0, "success": 0, "failed": 0},
            "CH04": {"total": 30, "completed": 0, "success": 0, "failed": 0},
        },
    }
    progress_file.write_text(json.dumps(initial_progress, ensure_ascii=False, indent=2), encoding="utf-8")
    
    # バックグラウンドでスクリプト実行
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "audio_tts_v2")
    subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "batch_regenerate_tts.py")],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    
    return {"status": "started", "message": "バッチTTS再生成を開始しました。"}


@app.get("/api/batch-tts/log")
def get_batch_tts_log(tail: int = Query(100, ge=10, le=1000)):
    """バッチTTS再生成のログを取得"""
    log_file = PROJECT_ROOT / "batch_tts_regeneration.log"
    if not log_file.exists():
        return PlainTextResponse("ログファイルがありません。バッチを開始してください。")
    try:
        lines = log_file.read_text(encoding="utf-8").splitlines()
        return PlainTextResponse("\n".join(lines[-tail:]))
    except Exception as exc:
        return PlainTextResponse(f"ログ読み込みエラー: {exc}")


@app.post("/api/batch-tts/reset")
def reset_batch_tts_progress():
    """バッチTTS進捗をリセット（待機状態に戻す）"""
    progress_file = PROJECT_ROOT / "batch_tts_progress.json"
    if progress_file.exists():
        try:
            data = json.loads(progress_file.read_text(encoding="utf-8"))
            # 実行中の場合はリセット不可
            if data.get("status") == "running":
                raise HTTPException(status_code=409, detail="実行中のバッチはリセットできません。")
        except json.JSONDecodeError:
            pass
        progress_file.unlink()
    return {"status": "reset", "message": "バッチ進捗をリセットしました。"}


@app.get("/api/ping")
def ping():
    return {"status": "ok"}


@app.get("/api/admin/lock-metrics", response_model=LockMetricsResponse)
def get_lock_metrics():
    history = [LockMetricSample(**entry) for entry in LOCK_HISTORY]
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with sqlite3.connect(LOCK_DB_PATH) as conn:
        aggregates = conn.execute(
            """
            SELECT substr(occurred_at, 1, 10) AS day,
                   SUM(CASE WHEN event_type = 'timeout' THEN 1 ELSE 0 END) AS timeout_count,
                   SUM(CASE WHEN event_type = 'unexpected' THEN 1 ELSE 0 END) AS unexpected_count
            FROM lock_metrics
            WHERE occurred_at >= ?
            GROUP BY day
            ORDER BY day DESC
            LIMIT 7
            """,
            (seven_days_ago,),
        ).fetchall()
    daily = [
        {"date": row[0], "timeout": row[1], "unexpected": row[2]}
        for row in aggregates
    ]
    return LockMetricsResponse(
        timeout=LOCK_METRICS["timeout"],
        unexpected=LOCK_METRICS["unexpected"],
        history=history,
        daily=daily,
    )



# ---------------------------------------------------------------------------
# Audio Integrity API
# ---------------------------------------------------------------------------

@app.get("/api/audio-check/recent")
def list_recent_audio_checks(limit: int = 10):
    """Find recently generated audio logs."""
    results = []
    if not DATA_ROOT.exists():
        return []
    
    # Search for log.json files in script_pipeline/data/CHxx/xxx/audio_prep/log.json
    for channel_dir in DATA_ROOT.iterdir():
        if not channel_dir.is_dir() or not channel_dir.name.startswith("CH"):
            continue
        for video_dir in channel_dir.iterdir():
            if not video_dir.is_dir() or not video_dir.name.isdigit():
                continue
            
            log_path = video_dir / "audio_prep" / "log.json"
            if log_path.exists():
                try:
                    stat = log_path.stat()
                    # Read basic info lightly if needed, or just return path metadata
                    # To be fast, just use mtime
                    results.append({
                        "channel": channel_dir.name,
                        "video": video_dir.name,
                        "mtime": stat.st_mtime,
                        "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
                    })
                except Exception:
                    continue
    
    # Sort by mtime desc
    results.sort(key=lambda x: x["mtime"], reverse=True)
    return results[:limit]

@app.get("/api/audio-check/{channel_id}/{video_id}")
def get_audio_integrity_log(channel_id: str, video_id: str):
    """Retrieve audio integrity logs from log.json."""
    # SCRIPT_PIPELINE_DATA_ROOT is defined in ui/server/main.py but not here.
    # Re-derive it or use DATA_ROOT which is script_pipeline/data
    log_path = DATA_ROOT / channel_id / video_id / "audio_prep" / "log.json"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Audio log not found. Run Strict Pipeline first.")
    
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse log.json: {e}")

@app.get("/api/kb")
def get_knowledge_base():
    """Retrieve Global Knowledge Base."""
    # Resolve path dynamically to ensure correct root
    root = Path(__file__).resolve().parents[2]
    real_kb_path = root / "audio_tts_v2" / "data" / "global_knowledge_base.json"
    
    if not real_kb_path.exists():
        logger.warning(f"KB not found at: {real_kb_path}")
        return {"version": 2, "words": {}}
    
    try:
        with open(real_kb_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Migrate/Compatibility
        if "entries" in data and "words" not in data:
             return {"version": 1, "words": {}} # Reset if old version
        return data
    except Exception as e:
        logger.error(f"Failed to load KB at {real_kb_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load KB: {e}")

class KnowledgeBaseUpsertRequest(BaseModel):
    word: str = Field(..., description="登録する単語（漢字/表記）")
    reading: str = Field(..., description="読み（カナ推奨）")


@app.post("/api/kb")
def upsert_knowledge_base_entry(payload: KnowledgeBaseUpsertRequest):
    """Add or update an entry in Global Knowledge Base (word dict)."""
    word = payload.word.strip()
    reading = payload.reading.strip()
    if is_banned_surface(word):
        raise HTTPException(status_code=400, detail="短すぎる/曖昧な単語は辞書登録できません。")
    if not reading:
        raise HTTPException(status_code=400, detail="読みを入力してください。")
    normalized = normalize_reading_kana(reading)
    if not is_safe_reading(normalized):
        raise HTTPException(status_code=400, detail="読みはカナで入力してください（漢字や説明文は不可）。")
    if normalized == word:
        raise HTTPException(status_code=400, detail="読みが表記と同じなので登録不要です。")
    reading = normalized

    kb_path = KB_PATH
    kb_path.parent.mkdir(parents=True, exist_ok=True)
    data: Dict[str, Any] = {"version": 2, "words": {}, "updated_at": datetime.now(timezone.utc).isoformat()}
    if kb_path.exists():
        try:
            data = json.loads(kb_path.read_text(encoding="utf-8"))
        except Exception:
            # fall back to empty structure
            data = {"version": 2, "words": {}, "updated_at": datetime.now(timezone.utc).isoformat()}

    container = data.get("words")
    if container is None:
        container = data.get("entries")
        if container is None:
            container = {}
        data["words"] = container
    if not isinstance(container, dict):
        container = {}
        data["words"] = container

    container[word] = reading
    data["version"] = data.get("version") or 2
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    kb_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data

@app.delete("/api/kb/{entry_key}")
def delete_knowledge_base_entry(entry_key: str):
    """Delete an entry from GKB."""
    root = Path(__file__).resolve().parents[2]
    real_kb_path = root / "audio_tts_v2" / "data" / "global_knowledge_base.json"

    if not real_kb_path.exists():
        raise HTTPException(status_code=404, detail="KB not found")
    
    try:
        with open(real_kb_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Support both structures or migrate
        container = data.get("words")
        if container is None:
             container = data.get("entries") # Old format fallback
        
        if container and entry_key in container:
            del container[entry_key]
            
            # Atomic write
            temp_path = real_kb_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            shutil.move(temp_path, real_kb_path)
            
            return {"success": True, "message": f"Deleted key {entry_key}"}
        else:
            raise HTTPException(status_code=404, detail="Entry key not found")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update KB: {e}")


class ChannelReadingUpsertRequest(BaseModel):
    surface: str = Field(..., description="表記（辞書キー）")
    reading_kana: str = Field(..., description="読み（カナ）")
    reading_hira: Optional[str] = Field(None, description="読み（ひらがな・任意）")
    voicevox_kana: Optional[str] = Field(None, description="Voicevox 元読み（任意）")
    accent_moras: Optional[List[str]] = Field(None, description="アクセントモーラ列（任意）")
    source: Optional[str] = Field("manual", description="登録元")


@app.get("/api/reading-dict/{channel}")
def get_channel_reading_dict_api(channel: str):
    channel_code = normalize_channel_code(channel)
    data = load_channel_reading_dict(channel_code)

    def _compute_mecab_kana(surface: str) -> str:
        try:
            tokens = tokenize_with_mecab(surface)
            parts: List[str] = []
            for tok in tokens:
                reading = tok.get("reading_mecab") or tok.get("surface") or ""
                parts.append(str(reading))
            return normalize_reading_kana("".join(parts))
        except Exception:
            return ""

    enriched: Dict[str, Dict[str, object]] = {}
    for surface, meta in data.items():
        meta_dict = dict(meta or {})
        mecab_kana = _compute_mecab_kana(surface)
        meta_dict["mecab_kana"] = mecab_kana
        voicevox_kana = meta_dict.get("voicevox_kana")
        if isinstance(voicevox_kana, str) and voicevox_kana:
            similarity, mora_diff, _ = calc_kana_mismatch_score(mecab_kana, voicevox_kana)
            meta_dict["similarity"] = similarity
            meta_dict["mora_diff"] = mora_diff
        enriched[surface] = meta_dict

    return enriched


@app.post("/api/reading-dict/{channel}")
def upsert_channel_reading_dict_api(channel: str, payload: ChannelReadingUpsertRequest):
    channel_code = normalize_channel_code(channel)
    surface = payload.surface.strip()
    reading_kana = payload.reading_kana.strip()
    reading_hira = (payload.reading_hira or "").strip() or reading_kana
    if is_banned_surface(surface):
        raise HTTPException(status_code=400, detail="短すぎる/曖昧な単語は辞書登録できません。")
    if not reading_kana:
        raise HTTPException(status_code=400, detail="読みを入力してください。")
    normalized_kana = normalize_reading_kana(reading_kana)
    normalized_hira = normalize_reading_kana(reading_hira)
    if not is_safe_reading(normalized_kana):
        raise HTTPException(status_code=400, detail="読みはカナで入力してください（漢字や説明文は不可）。")
    if normalized_kana == surface:
        raise HTTPException(status_code=400, detail="読みが表記と同じなので登録不要です。")
    entry = ReadingEntry(
        surface=surface,
        reading_hira=normalized_hira or normalized_kana,
        reading_kana=normalized_kana,
        voicevox_kana=(payload.voicevox_kana or "").strip() or None,
        accent_moras=payload.accent_moras,
        source=payload.source or "manual",
        last_updated="",
    )
    try:
        merged = merge_channel_readings(channel_code, {surface: entry})
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return merged


@app.delete("/api/reading-dict/{channel}/{surface}")
def delete_channel_reading_dict_entry_api(channel: str, surface: str):
    channel_code = normalize_channel_code(channel)
    key = surface.strip()
    current = load_channel_reading_dict(channel_code)
    if key not in current:
        raise HTTPException(status_code=404, detail="entry not found")
    current.pop(key, None)
    try:
        save_channel_reading_dict(channel_code, current)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"success": True}


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
        "ui.backend.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=args.reload_dirs,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
from ui.backend.tools.optional_fields_registry import (
    OPTIONAL_FIELDS,
    FIELD_KEYS,
    get_planning_section,
    update_planning_from_row,
)
SPREADSHEET_EXPORT_DIR = PROJECT_ROOT / "exports" / "spreadsheets"
def _generate_thumbnail_caption(image_path: Path) -> tuple[str, str, str]:
    settings = _get_ui_settings()
    llm = settings.get("llm", {})
    preferred_provider = (llm.get("caption_provider") or DEFAULT_CAPTION_PROVIDER).lower()
    # フェーズ設定で上書き（caption 用）
    phase_provider, phase_model = _resolve_phase_choice(
        llm,
        "caption",
        default_provider=preferred_provider,
        default_model=llm.get("openai_caption_model") or DEFAULT_OPENAI_CAPTION_MODEL,
        allowed_providers={"openai", "openrouter"},
    )
    if phase_provider in {"openai", "openrouter"}:
        preferred_provider = phase_provider
    sequence: List[str] = []
    if preferred_provider == "openrouter":
        sequence = ["openrouter", "openai"]
    else:
        sequence = ["openai", "openrouter"]
    errors: List[str] = []
    for provider in sequence:
        if provider == "openai":
            api_key = _get_effective_openai_key()
            model_name = phase_model if preferred_provider == "openai" else (llm.get("openai_caption_model") or DEFAULT_OPENAI_CAPTION_MODEL)
            if not api_key:
                errors.append("OpenAI APIキーが未設定です。")
                continue
            try:
                text, model = _describe_image_with_openai(image_path, api_key, model_name)
                return text, model, "openai"
            except HTTPException as exc:
                errors.append(f"OpenAI: {exc.detail}")
        else:
            api_key = _get_effective_openrouter_key()
            model_name = llm.get("openrouter_caption_model") or THUMBNAIL_CAPTION_DEFAULT_MODEL
            if not api_key:
                errors.append("OpenRouter APIキーが未設定です。")
                continue
            try:
                text, model = _describe_image_with_openrouter(image_path, api_key, model_name)
                return text, model, "openrouter"
            except HTTPException as exc:
                errors.append(f"OpenRouter: {exc.detail}")
    raise HTTPException(status_code=503, detail="; ".join(errors) or "サムネイル説明に失敗しました。")


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
            curated = []
            seen = set()
            for model in _load_llm_model_scores():
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
                "path": "ui/backend/main.py::_generate_thumbnail_caption",
                "prompt_source": "コード内(system+user)",
                "endpoint": "OpenAI(Azure) or OpenRouter",
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
                "path": "ui/backend/main.py::_init_natural_command_model",
                "prompt_source": "コード内 (短いシステム/ユーザ)",
                "endpoint": "OpenAI(Azure)/OpenRouter",
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
                "prompt_source": "prompts/llm_polish_template.txt + persona",
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
                "path": "commentary_02_srt2images_timeline/src/srt2images/nanobanana_client.py::_run_direct",
                "prompt_source": "呼び出し元渡し（固定プロンプトなし）",
                "endpoint": "Gemini 2.5 Flash Image Preview",
            },
            "context_analysis": {
                "label": "文脈解析",
                "role": "SRTセクション分割",
                "path": "commentary_02_srt2images_timeline/src/srt2images/llm_context_analyzer.py::LLMContextAnalyzer.analyze_story_sections",
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


# Progress CSV expose
@app.get("/api/progress/channels/{channel_code}")
def api_progress_channel(channel_code: str):
    repo_root = Path(__file__).resolve().parents[2]
    csv_path = repo_root / "progress" / "channels" / f"{channel_code}.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="progress csv not found")
    try:
        import csv
        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        # merge redo flags from status.json (default True when missing)
        for row in rows:
            video_num = row.get("動画番号") or row.get("video") or row.get("Video") or ""
            norm_video = normalize_video_number(video_num) if video_num else None
            if not norm_video:
                continue
            meta: Dict[str, Any] = {}
            try:
                st = load_status(channel_code, norm_video)
                meta = st.metadata or {}
                redo_script = meta.get("redo_script")
                redo_audio = meta.get("redo_audio")
                if redo_script is None:
                    redo_script = True
                if redo_audio is None:
                    redo_audio = True
                row["redo_script"] = bool(redo_script)
                row["redo_audio"] = bool(redo_audio)
                if meta.get("redo_note"):
                    row["redo_note"] = meta.get("redo_note")
            except Exception:
                row["redo_script"] = True
                row["redo_audio"] = True
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
                    thumbs = _find_thumbnails(channel_code, norm_video, title, limit=1)
                    if thumbs:
                        row["thumbnail_url"] = thumbs[0]["url"]
                        row["thumbnail_path"] = thumbs[0]["path"]
                except Exception:
                    pass
        return {"channel": channel_code, "rows": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to read csv: {e}")
