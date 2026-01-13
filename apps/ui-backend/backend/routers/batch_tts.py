from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from factory_common.paths import (
    logs_root as ssot_logs_root,
    repo_root as ssot_repo_root,
    script_data_root as ssot_script_data_root,
)

router = APIRouter(prefix="/api/batch-tts", tags=["batch-tts"])

logger = logging.getLogger("ui_backend")

REPO_ROOT = ssot_repo_root()
# NOTE: PROJECT_ROOT is treated as repo-root throughout the UI backend (legacy alias).
PROJECT_ROOT = REPO_ROOT
DATA_ROOT = ssot_script_data_root()
LOGS_ROOT = ssot_logs_root()
UI_LOG_DIR = LOGS_ROOT / "ui"


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


@router.get("/progress", response_model=BatchTtsProgressResponse)
def get_batch_tts_progress() -> BatchTtsProgressResponse:
    """バッチTTS再生成の進捗を取得"""
    progress_file = UI_LOG_DIR / "batch_tts_progress.json"
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


@router.post("/start")
async def start_batch_tts_regeneration(
    channels: List[str] = Body(default=["CH06", "CH02", "CH04"]),
    background_tasks: BackgroundTasks = None,
):
    """バッチTTS再生成をバックグラウンドで開始"""
    UI_LOG_DIR.mkdir(parents=True, exist_ok=True)
    progress_file = UI_LOG_DIR / "batch_tts_progress.json"
    log_file = UI_LOG_DIR / "batch_tts_regeneration.log"
    # 既に実行中かチェック
    if progress_file.exists():
        try:
            data = json.loads(progress_file.read_text(encoding="utf-8"))
            if data.get("status") == "running":
                raise HTTPException(status_code=409, detail="バッチTTS再生成は既に実行中です。")
        except json.JSONDecodeError:
            pass

    channels_norm = [str(ch).strip().upper() for ch in (channels or []) if str(ch).strip()]
    if not channels_norm:
        raise HTTPException(status_code=400, detail="channels is empty")

    # Count targets (best-effort) so UI can show a realistic progress bar immediately.
    per_channel_totals: Dict[str, Any] = {}
    total_targets = 0
    for ch in channels_norm:
        count = 0
        try:
            ch_dir = DATA_ROOT / ch
            if ch_dir.exists():
                for p in ch_dir.iterdir():
                    if p.is_dir() and p.name.isdigit():
                        count += 1
        except Exception:
            count = 0
        per_channel_totals[ch] = {"total": count, "completed": 0, "success": 0, "failed": 0}
        total_targets += count

    # 進捗初期化
    initial_progress = {
        "status": "running",
        "current_channel": None,
        "current_video": None,
        "completed": 0,
        "total": int(total_targets),
        "success": 0,
        "failed": 0,
        "current_step": "開始中...",
        "errors": [],
        "updated_at": datetime.now().isoformat(),
        "channels": per_channel_totals,
    }
    progress_file.write_text(json.dumps(initial_progress, ensure_ascii=False, indent=2), encoding="utf-8")

    # Clear previous log so the UI shows the current run only.
    try:
        log_file.write_text("", encoding="utf-8")
    except Exception:
        pass

    # バックグラウンドでスクリプト実行
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    base_paths = [str(PROJECT_ROOT), str(PROJECT_ROOT / "packages")]
    if pythonpath:
        base_paths.append(pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(base_paths)
    runner = PROJECT_ROOT / "scripts" / "batch_regenerate_tts.py"
    subprocess.Popen(
        [
            sys.executable,
            str(runner),
            "--progress-path",
            str(progress_file),
            "--log-path",
            str(log_file),
            *[arg for ch in channels_norm for arg in ("--channel", ch)],
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return {"status": "started", "message": "バッチTTS再生成を開始しました。"}


@router.get("/log")
def get_batch_tts_log(tail: int = Query(100, ge=10, le=1000)):
    """バッチTTS再生成のログを取得"""
    log_file = UI_LOG_DIR / "batch_tts_regeneration.log"
    if not log_file.exists():
        return PlainTextResponse("ログファイルがありません。バッチを開始してください。")
    try:
        lines = log_file.read_text(encoding="utf-8").splitlines()
        return PlainTextResponse("\n".join(lines[-tail:]))
    except Exception as exc:
        return PlainTextResponse(f"ログ読み込みエラー: {exc}")


@router.post("/reset")
def reset_batch_tts_progress():
    """バッチTTS進捗をリセット（待機状態に戻す）"""
    progress_file = UI_LOG_DIR / "batch_tts_progress.json"
    log_file = UI_LOG_DIR / "batch_tts_regeneration.log"
    if progress_file.exists():
        try:
            data = json.loads(progress_file.read_text(encoding="utf-8"))
            # 実行中の場合はリセット不可
            if data.get("status") == "running":
                raise HTTPException(status_code=409, detail="実行中のバッチはリセットできません。")
        except json.JSONDecodeError:
            pass
        progress_file.unlink()
    try:
        log_file.unlink()
    except Exception:
        pass
    return {"status": "reset", "message": "バッチ進捗をリセットしました。"}

