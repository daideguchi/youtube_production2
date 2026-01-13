from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import APIRouter, HTTPException

from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.main import (
    NaturalCommandRequest,
    NaturalCommandResponse,
    PROJECT_ROOT,
    PlanningInfoResponse,
    THUMBNAIL_PROJECTS_LOCK,
    THUMBNAIL_PROJECT_STATUSES,
    ThumbnailProgressResponse,
    ThumbnailVariantResponse,
    VideoDetailResponse,
    VideoCreateRequest,
    VideoFileReferences,
    VideoImagesProgressResponse,
    VideoSummaryResponse,
    _build_youtube_description,
    _character_count_from_a_text,
    _collect_disk_thumbnail_variants,
    _compose_tagged_tts,
    _derive_effective_stages,
    _derive_effective_video_status,
    _stage_status_value,
    _summarize_video_detail_artifacts,
    _default_status_payload,
    build_planning_payload,
    build_planning_payload_from_row,
    build_status_payload,
    ensure_expected_updated_at,
    get_planning_section,
    get_audio_duration_seconds,
    interpret_natural_command,
    list_video_dirs,
    load_status,
    load_status_optional,
    normalize_planning_video_number,
    normalize_audio_metadata,
    parse_iso_datetime,
    planning_store,
    planning_hash_from_row,
    iter_thumbnail_catches_from_row,
    sha1_file_bytes,
    safe_relative_path,
    save_status,
    resolve_audio_path,
    resolve_srt_path,
    resolve_text_file,
    update_planning_from_row,
    video_base_dir,
    append_audio_history_entry,
)
from factory_common.paths import audio_final_dir, video_runs_root as ssot_video_runs_root

router = APIRouter(prefix="/api/channels", tags=["channels"])


def _load_tts_content_for_command(channel_code: str, video_number: str) -> str:
    final_tts_snapshot = audio_final_dir(channel_code, video_number) / "a_text.txt"
    editable_tts_path = video_base_dir(channel_code, video_number) / "audio_prep" / "script_sanitized.txt"
    tts_plain_path = final_tts_snapshot if final_tts_snapshot.exists() else editable_tts_path
    return tts_plain_path.read_text(encoding="utf-8") if tts_plain_path.exists() else ""


@router.post("/{channel}/videos", status_code=201)
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


@router.post("/{channel}/videos/{video}/command", response_model=NaturalCommandResponse)
def run_natural_command(channel: str, video: str, payload: NaturalCommandRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)

    tts_content = _load_tts_content_for_command(channel_code, video_number)
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
        # Avoid circular import during app.include_router(): the helper is defined after
        # router wiring in backend.main, so import it lazily at runtime.
        from backend import main as backend_main

        _, document = backend_main._load_thumbnail_projects_document()
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


@router.get("/{channel}/videos", response_model=List[VideoSummaryResponse])
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
            else build_planning_payload_from_row(planning_row.raw)
            if planning_row
            else PlanningInfoResponse(creation_flag=None, fields=[])
        )
        response.append(
            VideoSummaryResponse(
                video=video_number,
                script_id=status.get("script_id") if status else planning_row.script_id if planning_row else None,
                title=metadata.get("sheet_title")
                or metadata.get("title")
                or (planning_row.raw.get("タイトル") if planning_row else "(draft)"),
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


@router.get("/{channel}/videos/{video}", response_model=VideoDetailResponse)
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
