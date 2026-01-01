from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Body

from factory_common import locks as coord_locks
from factory_common import paths as repo_paths

router = APIRouter(prefix="/api/meta", tags=["meta"])

REPO_ROOT = repo_paths.repo_root()

_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}


def _run_git(*args: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return None, str(exc)

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return None, err or f"git_failed({proc.returncode})"

    return (proc.stdout or "").strip() or None, None


def _collect_meta() -> Dict[str, Any]:
    sha, sha_err = _run_git("rev-parse", "--short", "HEAD")
    branch, branch_err = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    dirty_out, dirty_err = _run_git("status", "--porcelain=v1")

    return {
        "repo_root": str(REPO_ROOT),
        "git": {
            "sha": sha,
            "branch": branch,
            "dirty": bool(dirty_out),
            "errors": {k: v for k, v in {"sha": sha_err, "branch": branch_err, "dirty": dirty_err}.items() if v},
        },
        "process": {
            "pid": os.getpid(),
        },
        "time": {
            "server_now": time.time(),
        },
    }


@router.get("")
def get_meta():
    # Cache briefly to avoid running git on every UI navigation.
    now = time.time()
    cached_at = float(_CACHE.get("at") or 0.0)
    cached_value = _CACHE.get("value")
    if cached_value and now - cached_at < 3.0:
        return cached_value

    value = _collect_meta()
    _CACHE["at"] = now
    _CACHE["value"] = value
    return value


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_text_best_effort(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _sha1_text(text: str) -> str:
    h = hashlib.sha1()
    h.update(text.encode("utf-8", errors="ignore"))
    return h.hexdigest()


def _normalize_channel(raw: str) -> str:
    ch = str(raw or "").strip().upper()
    if not ch:
        raise HTTPException(status_code=400, detail="channel is required")
    if not (len(ch) == 4 and ch.startswith("CH") and ch[2:].isdigit()):
        raise HTTPException(status_code=400, detail=f"invalid channel: {ch}")
    return ch


def _normalize_video(raw: str) -> str:
    token = str(raw or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="video is required")
    digits = "".join([c for c in token if c.isdigit()])
    if not digits:
        raise HTTPException(status_code=400, detail=f"invalid video: {token}")
    if len(digits) > 3:
        raise HTTPException(status_code=400, detail=f"invalid video: {token}")
    return digits.zfill(3)


def _load_json_best_effort(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(_read_text_best_effort(path))
    except Exception:
        return None


def _assembled_path_for_episode(channel: str, video: str) -> Path:
    base = repo_paths.video_root(channel, video) / "content"
    # Prefer assembled_human when present, else assembled.
    if (base / "assembled_human.md").exists():
        return base / "assembled_human.md"
    return base / "assembled.md"


def _compute_current_script_hash(channel: str, video: str) -> str:
    path = _assembled_path_for_episode(channel, video)
    if not path.exists():
        return ""
    text = _read_text_best_effort(path)
    if not text:
        return ""
    return _sha1_text(text)


def _dialog_audit_item_from_status(*, channel: str, video: str, status_obj: dict[str, Any]) -> Optional[dict[str, Any]]:
    meta = status_obj.get("metadata") if isinstance(status_obj.get("metadata"), dict) else {}
    audit = meta.get("dialog_ai_audit") if isinstance(meta.get("dialog_ai_audit"), dict) else None
    if not audit:
        return None

    verdict = str(audit.get("verdict") or "").strip() or None
    audited_at = str(audit.get("audited_at") or "").strip() or None
    audited_by = str(audit.get("audited_by") or "").strip() or None
    reasons = audit.get("reasons") if isinstance(audit.get("reasons"), list) else []
    notes = str(audit.get("notes") or "").strip() or ""
    script_hash_sha1 = str(audit.get("script_hash_sha1") or "").strip() or None

    current_hash = _compute_current_script_hash(channel, video)
    stale = bool(script_hash_sha1 and current_hash and script_hash_sha1 != current_hash)

    script_id = str(status_obj.get("script_id") or f"{channel}-{video}").strip() or f"{channel}-{video}"

    return {
        "schema": "ytm.meta.dialog_ai_audit.item.v1",
        "script_id": script_id,
        "channel": channel,
        "video": video,
        "verdict": verdict,
        "audited_at": audited_at,
        "audited_by": audited_by,
        "reasons": [str(r) for r in reasons if str(r).strip()],
        "notes": notes,
        "script_hash_sha1": script_hash_sha1,
        "stale": stale,
    }


@router.get("/dialog_ai_audit/{channel}")
def get_dialog_ai_audit_channel(channel: str):
    ch = _normalize_channel(channel)
    channel_dir = repo_paths.script_data_root() / ch
    items: list[dict[str, Any]] = []
    if channel_dir.exists():
        for status_path in sorted(channel_dir.glob("[0-9][0-9][0-9]/status.json")):
            video = status_path.parent.name
            status_obj = _load_json_best_effort(status_path)
            if not status_obj:
                continue
            item = _dialog_audit_item_from_status(channel=ch, video=video, status_obj=status_obj)
            if item:
                items.append(item)

    return {
        "schema": "ytm.meta.dialog_ai_audit.channel.v1",
        "generated_at": _utc_now_iso(),
        "channel": ch,
        "items": items,
    }


@router.get("/dialog_ai_audit/{channel}/{video}")
def get_dialog_ai_audit_video(channel: str, video: str):
    ch = _normalize_channel(channel)
    vid = _normalize_video(video)
    status_path = repo_paths.status_path(ch, vid)
    status_obj = _load_json_best_effort(status_path)
    if not status_obj:
        return {
            "schema": "ytm.meta.dialog_ai_audit.video.v1",
            "generated_at": _utc_now_iso(),
            "channel": ch,
            "video": vid,
            "found": False,
            "item": None,
        }

    item = _dialog_audit_item_from_status(channel=ch, video=vid, status_obj=status_obj)
    return {
        "schema": "ytm.meta.dialog_ai_audit.video.v1",
        "generated_at": _utc_now_iso(),
        "channel": ch,
        "video": vid,
        "found": bool(item),
        "item": item,
    }


@router.post("/script_reset/{channel}/{video}")
def reset_script_from_ui(channel: str, video: str, payload: dict[str, Any] = Body(default_factory=dict)):
    """
    Reset (delete) script artifacts for an episode and reinitialize status.json.
    This is destructive and intended for UI-triggered cleanup when restarting from scratch is faster.
    """
    ch = _normalize_channel(channel)
    vid = _normalize_video(video)

    status_path = repo_paths.status_path(ch, vid)
    status_obj = _load_json_best_effort(status_path) or {}
    meta = status_obj.get("metadata") if isinstance(status_obj.get("metadata"), dict) else {}

    if bool(meta.get("published_lock")):
        raise HTTPException(status_code=400, detail="published_lock=true (reset is blocked)")

    active_locks = coord_locks.default_active_locks_for_mutation()
    blocking = coord_locks.find_blocking_lock(status_path, active_locks)
    if blocking:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "blocked_by_lock",
                "lock": {"id": blocking.lock_id, "created_by": blocking.created_by, "mode": blocking.mode},
            },
        )

    wipe_research = bool(payload.get("wipe_research"))
    try:
        from script_pipeline.runner import reset_video
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed_to_import_script_pipeline.runner.reset_video: {exc}")

    try:
        reset_video(ch, vid, wipe_research=wipe_research)
    except SystemExit as exc:
        raise HTTPException(status_code=500, detail=f"reset_failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"reset_failed: {exc}")

    return {
        "ok": True,
        "schema": "ytm.meta.script_reset.v1",
        "reset_at": _utc_now_iso(),
        "channel": ch,
        "video": vid,
        "wipe_research": wipe_research,
        "status_path": status_path.as_posix(),
    }
