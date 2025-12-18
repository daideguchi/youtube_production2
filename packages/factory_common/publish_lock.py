from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from factory_common.paths import channels_csv_path, repo_root, status_path

try:  # optional dependency (used elsewhere in repo)
    import portalocker  # type: ignore
except Exception:  # pragma: no cover
    portalocker = None  # type: ignore


PUBLISHED_PROGRESS_VALUE = "投稿済み"
_LOCK_TIMEOUT_SECONDS = 10


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_today_ymd() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _normalize_video_token(value: str | None) -> Optional[str]:
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return None
    try:
        return f"{int(digits):03d}"
    except ValueError:
        return None


def _is_published_progress(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    return PUBLISHED_PROGRESS_VALUE in text or "公開済み" in text or text.lower() in {"published", "posted"}


def _legacy_progress_csv_path(channel: str) -> Path:
    return repo_root() / "progress" / "channels" / f"{str(channel).upper()}.csv"


def _read_csv_rows(path: Path) -> Tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def _write_csv_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = [{key: (row.get(key) or "") for key in fieldnames} for row in rows]
    if portalocker is None:
        with path.open("w", encoding="utf-8", newline="") as handle:  # pragma: no cover - fallback
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(serialised)
        return

    with portalocker.Lock(  # type: ignore[attr-defined]
        str(path),
        mode="w",
        encoding="utf-8",
        timeout=_LOCK_TIMEOUT_SECONDS,
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(serialised)
        handle.flush()


def _iter_row_video_tokens(row: dict[str, str]) -> Iterable[str]:
    for key in ("動画番号", "No.", "VideoNumber", "video", "Video"):
        token = _normalize_video_token(row.get(key) or "")
        if token:
            yield token


def _find_row(rows: list[dict[str, str]], video_token: str) -> Optional[dict[str, str]]:
    for row in rows:
        if any(tok == video_token for tok in _iter_row_video_tokens(row)):
            return row
    return None


def is_episode_published_locked(channel: str, video: str) -> bool:
    ch = str(channel).upper()
    token = _normalize_video_token(video)
    if not token:
        return False

    for path in (channels_csv_path(ch), _legacy_progress_csv_path(ch)):
        if not path.exists():
            continue
        try:
            _, rows = _read_csv_rows(path)
        except Exception:
            continue
        row = _find_row(rows, token)
        if row and _is_published_progress(row.get("進捗")):
            return True

    sp = status_path(ch, token)
    if sp.exists():
        try:
            payload = json.loads(sp.read_text(encoding="utf-8"))
            meta = payload.get("metadata") if isinstance(payload, dict) else None
            if isinstance(meta, dict) and bool(meta.get("published_lock")):
                return True
        except Exception:
            return False
    return False


@dataclass(frozen=True)
class PublishLockResult:
    channel: str
    video: str
    published_at: str
    updated_csv_paths: tuple[str, ...]
    status_updated: bool


def mark_episode_published_locked(
    channel: str,
    video: str,
    *,
    force_complete: bool = True,
    published_at: Optional[str] = None,
    update_legacy_progress_csv: bool = True,
    update_status_json: bool = True,
) -> PublishLockResult:
    ch = str(channel).upper()
    token = _normalize_video_token(video)
    if not token:
        raise ValueError(f"invalid video: {video}")
    publish_date = (published_at or "").strip() or _utc_today_ymd()

    paths: list[Path] = [channels_csv_path(ch)]
    if update_legacy_progress_csv:
        paths.append(_legacy_progress_csv_path(ch))

    updated_csv_paths: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        fieldnames, rows = _read_csv_rows(path)
        row = _find_row(rows, token)
        if not row:
            continue
        if "進捗" not in fieldnames:
            fieldnames.append("進捗")
        row["進捗"] = PUBLISHED_PROGRESS_VALUE

        if force_complete:
            force_map = {
                "音声整形": "済",
                "音声検証": f"完了 (forced) {publish_date}",
                "音声生成": f"完了 (forced) {publish_date}",
                "音声品質": f"完了 (forced) {publish_date}",
                "納品": f"投稿済み {publish_date}",
            }
            for col, val in force_map.items():
                if col not in fieldnames:
                    continue
                if not (row.get(col) or "").strip():
                    row[col] = val

        if "更新日時" in fieldnames:
            row["更新日時"] = _utc_now_iso()

        _write_csv_rows(path, fieldnames, rows)
        updated_csv_paths.append(str(path))

    status_updated = False
    if update_status_json:
        sp = status_path(ch, token)
        if sp.exists():
            try:
                payload = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            meta = payload.get("metadata")
            if not isinstance(meta, dict):
                meta = {}
            meta["published_lock"] = True
            meta["published_at"] = publish_date
            if force_complete:
                meta["redo_script"] = False
                meta["redo_audio"] = False
            payload["metadata"] = meta
            payload["updated_at"] = _utc_now_iso()
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            status_updated = True

    return PublishLockResult(
        channel=ch,
        video=token,
        published_at=publish_date,
        updated_csv_paths=tuple(updated_csv_paths),
        status_updated=status_updated,
    )


@dataclass(frozen=True)
class PublishUnlockResult:
    channel: str
    video: str
    updated_csv_paths: tuple[str, ...]
    status_updated: bool


def unmark_episode_published_locked(
    channel: str,
    video: str,
    *,
    restore_progress: Optional[str] = None,
    update_legacy_progress_csv: bool = True,
    update_status_json: bool = True,
) -> PublishUnlockResult:
    """
    Clear the "published_lock" guard when it was set by mistake.

    Policy:
      - If planning/legacy CSV "進捗" is marked as 投稿済み/公開済み, clear it (or set restore_progress).
      - Always clear status.json metadata.published_lock when update_status_json=True.
      - This is an operator override; it does NOT attempt to reconstruct prior progress fields.
    """
    ch = str(channel).upper()
    token = _normalize_video_token(video)
    if not token:
        raise ValueError(f"invalid video: {video}")

    paths: list[Path] = [channels_csv_path(ch)]
    if update_legacy_progress_csv:
        paths.append(_legacy_progress_csv_path(ch))

    updated_csv_paths: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        fieldnames, rows = _read_csv_rows(path)
        row = _find_row(rows, token)
        if not row:
            continue
        progress = row.get("進捗")
        if _is_published_progress(progress):
            row["進捗"] = (restore_progress or "").strip()
            if "更新日時" in fieldnames:
                row["更新日時"] = _utc_now_iso()
            _write_csv_rows(path, fieldnames, rows)
            updated_csv_paths.append(str(path))

    status_updated = False
    if update_status_json:
        sp = status_path(ch, token)
        if sp.exists():
            try:
                payload = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            meta = payload.get("metadata")
            if not isinstance(meta, dict):
                meta = {}
            meta["published_lock"] = False
            meta.pop("published_at", None)
            payload["metadata"] = meta
            payload["updated_at"] = _utc_now_iso()
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            status_updated = True

    return PublishUnlockResult(
        channel=ch,
        video=token,
        updated_csv_paths=tuple(updated_csv_paths),
        status_updated=status_updated,
    )
