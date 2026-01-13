from __future__ import annotations

import csv
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from backend.app.normalize import normalize_channel_code
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

