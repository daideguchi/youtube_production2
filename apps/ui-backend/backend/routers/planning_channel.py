from __future__ import annotations

import csv
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from backend.app.normalize import normalize_channel_code, normalize_optional_text
from backend.main import (
    PlanningCsvRowResponse,
    PlanningProgressUpdateRequest,
    build_planning_payload_from_row,
    current_timestamp,
    _normalize_video_number_token,
    _read_channel_csv_rows,
    _write_csv_with_lock,
)
from backend.core.tools import thumbnails_lookup as thumbnails_lookup_tools
from factory_common.alignment import (
    iter_thumbnail_catches_from_row,
    planning_hash_from_row,
    sha1_file as sha1_file_bytes,
)
from factory_common.paths import planning_root as ssot_planning_root
from factory_common.paths import script_data_root as ssot_script_data_root
from factory_common.publish_lock import is_episode_published_locked

router = APIRouter(prefix="/api/planning", tags=["planning"])

DATA_ROOT = ssot_script_data_root()
CHANNEL_PLANNING_DIR = ssot_planning_root() / "channels"


@router.get("/channels/{channel_code}")
def get_planning_channel_rows(channel_code: str):
    """
    Viewer-friendly Planning CSV rows.

    NOTE: Keep behavior compatible with legacy `backend.main.api_planning_channel`.
    """
    from backend.main import load_status, normalize_planning_video_number

    channel_code = normalize_channel_code(channel_code)
    csv_path = CHANNEL_PLANNING_DIR / f"{channel_code}.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="planning csv not found")
    try:
        with csv_path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to read csv: {exc}") from exc


@router.put(
    "/channels/{channel_code}/{video_number}/progress",
    response_model=PlanningCsvRowResponse,
)
def update_planning_channel_progress(
    channel_code: str,
    video_number: str,
    payload: PlanningProgressUpdateRequest,
):
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
