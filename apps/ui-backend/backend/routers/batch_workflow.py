from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from backend.app.normalize import normalize_channel_code, normalize_video_number
from factory_common.paths import logs_root as ssot_logs_root, repo_root as ssot_repo_root

router = APIRouter(prefix="/api/batch-workflow", tags=["batch-workflow"])

logger = logging.getLogger(__name__)

PROJECT_ROOT = ssot_repo_root()
LOGS_ROOT = ssot_logs_root()
#
# NOTE:
# - LOGS_ROOT is derived from workspace_root(), which may point to a shared/Vault path
#   on always-on hosts (e.g., Acer reading Vault SoT).
# - This router uses sqlite for UI task/queue state; sqlite on network shares is
#   prone to "database is locked" issues.
# - Therefore, keep these UI runtime logs/db LOCAL to the repo root, not the Vault.
UI_LOG_DIR = PROJECT_ROOT / "workspaces" / "logs" / "ui"
TASK_LOG_DIR = UI_LOG_DIR / "batch_workflow"
TASK_DB_PATH = UI_LOG_DIR / "ui_tasks.db"
TASK_TABLE = "batch_tasks"
QUEUE_TABLE = "batch_queue"
QUEUE_CONFIG_DIR = TASK_LOG_DIR / "queue_configs"
QUEUE_PROGRESS_DIR = TASK_LOG_DIR / "queue_progress"


def current_timestamp() -> str:
    """Return an ISO8601 UTC timestamp with ``Z`` suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid JSON: {path}") from exc


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


class BatchWorkflowConfig(BaseModel):
    min_characters: int = Field(8000, ge=1000)
    max_characters: int = Field(12000, ge=1000)
    script_prompt_template: Optional[str] = None
    quality_check_template: Optional[str] = None
    llm_slot: Optional[int] = Field(
        None,
        ge=0,
        description="推奨: 数字スロット（LLM_MODEL_SLOT）。未指定ならデフォルトslot。",
    )
    exec_slot: Optional[int] = Field(
        None,
        ge=0,
        description="推奨: 実行スロット（LLM_EXEC_SLOT）。未指定ならデフォルトslot。",
    )
    llm_model: Optional[str] = Field(
        None,
        description="[deprecated] モデル名/キーでの上書き。ブレ防止のため通常運用では禁止。数字だけ指定した場合は slot として解釈。",
    )
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
        cleaned: List[str] = []
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
        cleaned: List[str] = []
        for value in values:
            value = value.strip()
            if not value.isdigit():
                raise HTTPException(status_code=400, detail="video_numbers には数値のみ指定してください。")
            cleaned.append(value.zfill(3))
        if not cleaned:
            raise HTTPException(status_code=400, detail="video_numbers を1件以上指定してください。")
        return cleaned


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

    # NOTE: Do not auto-fill llm_model here.
    # Model routing must be controlled by numeric slots (llm_slot / LLM_MODEL_SLOT) to prevent drift.

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
    # NOTE: Batch workflow is executed by calling the canonical ops entrypoint.
    # Avoid hardcoding package-internal legacy paths (e.g. qwen/batch_workflow.py).
    runbook = PROJECT_ROOT / "scripts" / "ops" / "script_runbook.py"
    cmd = [
        sys.executable,
        str(runbook),
        "new",
        "--channel",
        channel_code,
        "--video",
        video_number,
        "--until",
        "script_validation",
    ]
    if getattr(config, "llm_slot", None) is not None:
        cmd.extend(["--llm-slot", str(int(config.llm_slot))])
    if getattr(config, "exec_slot", None) is not None:
        cmd.extend(["--exec-slot", str(int(config.exec_slot))])
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

    # Legacy compatibility: llm_model accepts numeric-only as slot id.
    # Non-numeric values are forbidden to prevent drift across agents.
    raw_legacy_slot = str(getattr(config, "llm_model", "") or "").strip()
    if raw_legacy_slot:
        if raw_legacy_slot.isdigit():
            if getattr(config, "llm_slot", None) is None:
                config.llm_slot = int(raw_legacy_slot)
        else:
            raise RuntimeError(
                "Forbidden batch config: llm_model must be numeric slot (LLM_MODEL_SLOT). "
                "Use config.llm_slot / --llm-slot instead."
            )

    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            for index, video in enumerate(video_numbers):
                if queue_id:
                    _update_queue_progress(queue_id, processed=index, current_video=f"{channel_code}-{video}")

                log_file.write(f"=== {datetime.now().isoformat()} / {channel_code}-{video} ===\n")
                log_file.flush()

                cmd = _build_batch_command(channel_code, video, config, config_path)
                env = os.environ.copy()
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(PROJECT_ROOT),
                    env=env,
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
                        log_file.write("⚠️ ループモード有効: エラーを無視して次の動画へ進みます。\n\n")
                        log_file.flush()
                        # 失敗しても processed カウントは進める（処理済みとして扱う）
                        if queue_id:
                            _update_queue_progress(queue_id, processed=index + 1, current_video=None)
                        continue
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


@router.post("/start", response_model=BatchWorkflowTaskResponse)
async def start_batch_workflow(payload: BatchWorkflowRequest):
    channel_code = normalize_channel_code(payload.channel_code)
    video_numbers = [normalize_video_number(v) for v in payload.video_numbers]
    # Guard: prevent drift across agents by forbidding non-slot model overrides.
    if payload.config and getattr(payload.config, "llm_model", None):
        raw = str(getattr(payload.config, "llm_model") or "").strip()
        if raw and not raw.isdigit():
            raise HTTPException(
                status_code=400,
                detail="llm_model は禁止です（ブレ防止）。数字スロット（llm_slot / LLM_MODEL_SLOT）を指定してください。",
            )
    if _channel_is_busy(channel_code):
        raise HTTPException(
            status_code=409,
            detail=f"{channel_code} は別のバッチを実行中です。キューが完了するまでお待ちください。",
        )
    task_id = _launch_batch_task(channel_code, video_numbers, payload.config)
    return _task_response(task_id, TASK_REGISTRY[task_id])


@router.post("/queue", response_model=BatchQueueEntryResponse)
async def enqueue_batch_workflow(payload: BatchQueueRequest):
    channel_code = normalize_channel_code(payload.channel_code)
    video_numbers = [normalize_video_number(v) for v in payload.video_numbers]
    if payload.config and getattr(payload.config, "llm_model", None):
        raw = str(getattr(payload.config, "llm_model") or "").strip()
        if raw and not raw.isdigit():
            raise HTTPException(
                status_code=400,
                detail="llm_model は禁止です（ブレ防止）。数字スロット（llm_slot / LLM_MODEL_SLOT）を指定してください。",
            )
    entry_id = insert_queue_entry(channel_code, video_numbers, payload.config)
    entry = get_queue_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=500, detail="キュー登録に失敗しました。")
    asyncio.create_task(_maybe_start_queue(channel_code))
    return _build_queue_response(entry)


@router.get("/queue", response_model=List[BatchQueueEntryResponse])
def list_batch_queue(channel: Optional[str] = Query(None)):
    entries = list_queue_entries(channel)
    return [_build_queue_response(entry) for entry in entries]


def cancel_background_task(_task_id: Any) -> None:
    """Best-effort placeholder (no subprocess cancellation)."""
    return


@router.post("/queue/{entry_id}/cancel", response_model=BatchQueueEntryResponse)
def cancel_queue_entry(entry_id: int):
    entry = get_queue_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="queue entry not found")
    if entry["status"] == QueueEntryStatus.running.value:
        # running でもキャンセルを許可し、バックグラウンドタスクを停止（best-effort）
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


@router.get("/{task_id}", response_model=BatchWorkflowTaskResponse)
def get_batch_workflow(task_id: str):
    record = TASK_REGISTRY.get(task_id)
    if record:
        return _task_response(
            task_id,
            {
                "channel": record["channel_code"],
                "videos": record["video_numbers"],
                "status": record["status"],
                "log_path": record.get("log_path"),
                "config_path": record.get("config_path"),
                "created_at": record.get("created_at"),
            },
        )
    db_record = load_task_record(task_id)
    if not db_record:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_response(task_id, db_record)


@router.get("/{task_id}/log", response_model=BatchWorkflowLogResponse)
def get_batch_workflow_log(task_id: str, tail: int = Query(200, ge=1, le=2000)):
    record = TASK_REGISTRY.get(task_id) or load_task_record(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="task not found")
    log_path = Path(record.get("log_path") or "")
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="log file not found")
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return BatchWorkflowLogResponse(task_id=task_id, lines=lines[-tail:])
