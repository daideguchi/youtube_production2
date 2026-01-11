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
from backend.app.youtube_client import YouTubeDataClient, YouTubeDataAPIError

router = APIRouter(prefix="/api/meta", tags=["meta"])

REPO_ROOT = repo_paths.repo_root()

_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}
_YOUTUBE_PUBLISHING_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}

LOGS_ROOT = repo_paths.logs_root()
YOUTUBE_PUBLISHING_CACHE_DIR = LOGS_ROOT / "_cache" / "youtube_publishing"
YOUTUBE_PUBLISHING_CACHE_PATH = YOUTUBE_PUBLISHING_CACHE_DIR / "publishing.json"


def _float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


YOUTUBE_PUBLISHING_CACHE_TTL_SEC = _float_env("YOUTUBE_PUBLISHING_CACHE_TTL_HOURS", 6.0) * 3600.0


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


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _load_youtube_publishing_disk_cache(*, now_ts: float) -> tuple[Optional[float], Optional[dict[str, Any]]]:
    if not YOUTUBE_PUBLISHING_CACHE_PATH.exists():
        return None, None
    try:
        payload = json.loads(_read_text_best_effort(YOUTUBE_PUBLISHING_CACHE_PATH))
    except Exception:
        return None, None
    fetched_at = _parse_iso_datetime(payload.get("fetched_at"))
    if not fetched_at:
        return None, None
    fetched_ts = fetched_at.replace(tzinfo=timezone.utc).timestamp()
    if now_ts - fetched_ts > YOUTUBE_PUBLISHING_CACHE_TTL_SEC:
        return None, None
    return fetched_ts, payload


def _persist_youtube_publishing_disk_cache(payload: dict[str, Any]) -> None:
    try:
        YOUTUBE_PUBLISHING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        YOUTUBE_PUBLISHING_CACHE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        # best-effort
        return


def _jst_date_from_iso(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Tokyo")
    except Exception:
        tz = timezone.utc
    return dt.astimezone(tz).date().isoformat()


def _load_channels_catalog() -> list[dict[str, Any]]:
    catalog_path = REPO_ROOT / "packages" / "script_pipeline" / "channels" / "channels_info.json"
    if not catalog_path.exists():
        return []
    try:
        text = _read_text_best_effort(catalog_path)
        obj = json.loads(text)
    except Exception:
        return []
    return obj if isinstance(obj, list) else []


def _channel_sort_key(code: str) -> tuple[int, str]:
    text = str(code or "").upper()
    if text.startswith("CH") and text[2:].isdigit():
        return (int(text[2:]), text)
    return (10_000, text)


def _chunks(seq: list[str], n: int) -> list[list[str]]:
    if n <= 0:
        return [list(seq)]
    return [seq[i : i + n] for i in range(0, len(seq), n)]


def _resolve_uploads_playlists(
    client: YouTubeDataClient,
    channel_ids: list[str],
) -> tuple[dict[str, str], list[str]]:
    warnings: list[str] = []
    out: dict[str, str] = {}
    for batch in _chunks(channel_ids, 50):
        try:
            data = client._get(
                "channels",
                {
                    "part": "contentDetails",
                    "id": ",".join(batch),
                    "maxResults": max(1, len(batch)),
                },
            )
        except YouTubeDataAPIError as exc:
            warnings.append(str(exc))
            continue

        items = data.get("items") or []
        for item in items:
            channel_id = str(item.get("id") or "").strip()
            uploads = (
                (item.get("contentDetails") or {}).get("relatedPlaylists") or {}
            ).get("uploads")
            uploads = str(uploads or "").strip()
            if channel_id and uploads:
                out[channel_id] = uploads

    return out, warnings


def _fetch_latest_uploads(
    client: YouTubeDataClient,
    uploads_playlist_id: str,
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    try:
        data = client._get(
            "playlistItems",
            {
                "part": "contentDetails,snippet",
                "playlistId": uploads_playlist_id,
                "maxResults": max(1, min(limit, 6)),
            },
        )
    except YouTubeDataAPIError as exc:
        return [], str(exc)

    items = data.get("items") or []
    out: list[dict[str, Any]] = []
    for item in items:
        snippet = item.get("snippet") or {}
        content = item.get("contentDetails") or {}
        video_id = str(content.get("videoId") or (snippet.get("resourceId") or {}).get("videoId") or "").strip() or None
        title = str(snippet.get("title") or "").strip() or None
        published_at = str(content.get("videoPublishedAt") or snippet.get("publishedAt") or "").strip() or None
        out.append(
            {
                "video_id": video_id,
                "title": title,
                "published_at": published_at,
                "published_date_jst": _jst_date_from_iso(published_at),
                "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
            }
        )
    return out, None


@router.get("/youtube/publishing")
def get_youtube_publishing(
    refresh: bool = False,
    limit: int = 1,
):
    """
    YouTube Data API（APIキー）で、各チャンネルの最新公開日（公開済み動画）を集計する。
    Note: 公開予約（publishAt）は APIキーでは取得できないため対象外。
    """
    now = time.time()
    cached_at = float(_YOUTUBE_PUBLISHING_CACHE.get("at") or 0.0)
    cached_value = _YOUTUBE_PUBLISHING_CACHE.get("value")
    if cached_value and not refresh and now - cached_at < YOUTUBE_PUBLISHING_CACHE_TTL_SEC:
        return cached_value
    if not refresh:
        disk_at, disk_value = _load_youtube_publishing_disk_cache(now_ts=now)
        if disk_value is not None and disk_at is not None:
            _YOUTUBE_PUBLISHING_CACHE["at"] = disk_at
            _YOUTUBE_PUBLISHING_CACHE["value"] = disk_value
            return disk_value

    client = YouTubeDataClient.from_env()
    if not getattr(client, "api_key", None):
        return {
            "schema": "ytm.meta.youtube_publishing.v1",
            "status": "disabled",
            "generated_at": _utc_now_iso(),
            "fetched_at": None,
            "channels": [],
            "warnings": ["YOUTUBE_API_KEY が設定されていません"],
        }

    catalog = _load_channels_catalog()
    warnings: list[str] = []
    targets: list[dict[str, Any]] = []
    channel_ids: list[str] = []

    for entry in catalog:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("channel_id") or "").strip().upper()
        if not code.startswith("CH"):
            continue
        youtube = entry.get("youtube") if isinstance(entry.get("youtube"), dict) else {}
        channel_id = str(youtube.get("channel_id") or "").strip()
        handle = str(entry.get("youtube_handle") or youtube.get("handle") or "").strip() or None
        if not channel_id:
            warnings.append(f"missing_youtube_channel_id:{code}")
            continue
        channel_ids.append(channel_id)
        targets.append({"channel": code, "youtube_channel_id": channel_id, "youtube_handle": handle})

    uploads_map, uploads_warnings = _resolve_uploads_playlists(client, channel_ids)
    warnings.extend(uploads_warnings)

    limit_clamped = max(1, min(int(limit or 1), 6))

    channels_out: list[dict[str, Any]] = []
    for item in targets:
        code = str(item.get("channel") or "").strip().upper()
        channel_id = str(item.get("youtube_channel_id") or "").strip()
        uploads_playlist_id = uploads_map.get(channel_id)
        if not uploads_playlist_id:
            warnings.append(f"missing_uploads_playlist:{code}")
            continue

        recent, err = _fetch_latest_uploads(client, uploads_playlist_id, limit=limit_clamped)
        if err:
            warnings.append(f"{code}: {err}")
        latest = recent[0] if recent else {}
        channels_out.append(
            {
                "channel": code,
                "youtube_channel_id": channel_id,
                "youtube_handle": item.get("youtube_handle"),
                "latest_published_at": latest.get("published_at"),
                "latest_published_date_jst": latest.get("published_date_jst"),
                "latest_title": latest.get("title"),
                "latest_video_id": latest.get("video_id"),
                "latest_url": latest.get("url"),
                "recent": recent,
            }
        )

    channels_out.sort(key=lambda x: _channel_sort_key(str(x.get("channel") or "")))
    payload = {
        "schema": "ytm.meta.youtube_publishing.v1",
        "status": "ok",
        "generated_at": _utc_now_iso(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "channels": channels_out,
        "warnings": warnings[:200],
    }
    _persist_youtube_publishing_disk_cache(payload)
    _YOUTUBE_PUBLISHING_CACHE["at"] = now
    _YOUTUBE_PUBLISHING_CACHE["value"] = payload
    return payload


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
