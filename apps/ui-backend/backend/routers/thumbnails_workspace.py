from __future__ import annotations

import io
import json
import mimetypes
import re
import urllib.parse
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse

from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.app.path_utils import safe_relative_path
from backend.main import (
    ThumbnailChannelBlockResponse,
    ThumbnailChannelSummaryResponse,
    ThumbnailChannelVideoResponse,
    ThumbnailDescriptionResponse,
    ThumbnailLibraryAssetResponse,
    ThumbnailLibraryAssignRequest,
    ThumbnailLibraryAssignResponse,
    ThumbnailLibraryImportRequest,
    ThumbnailLibraryRenameRequest,
    ThumbnailOverviewResponse,
    ThumbnailProjectResponse,
    ThumbnailProjectUpdateRequest,
    ThumbnailQuickHistoryEntry,
    ThumbnailThumbSpecResponse,
    ThumbnailThumbSpecUpdateRequest,
    ThumbnailTextLineSpecLinePayload,
    ThumbnailTextLineSpecResponse,
    ThumbnailTextLineSpecUpdateRequest,
    ThumbnailElementPayload,
    ThumbnailElementsSpecResponse,
    ThumbnailElementsSpecUpdateRequest,
    ThumbnailElementStrokePayload,
    ThumbnailVariantResponse,
)
from backend.app.channel_info_store import refresh_channel_info

router = APIRouter(prefix="/api/workspaces/thumbnails", tags=["thumbnails"])


@router.get("", response_model=ThumbnailOverviewResponse)
def get_thumbnail_overview():
    from backend import main as backend_main

    projects_path = backend_main._resolve_thumbnail_projects_path()

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

    remaining_refresh_budget = max(0, backend_main.YOUTUBE_UPLOADS_MAX_REFRESH_PER_REQUEST)
    merged_channels: set[str] = set()

    for channel_code, info in channel_info_map.items():
        entry = channel_map.setdefault(channel_code, {"projects": [], "videos": []})
        if channel_code not in merged_channels:
            backend_main._merge_disk_thumbnail_variants(channel_code, entry)
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

        backoff_until = backend_main.YOUTUBE_UPLOAD_FAILURE_STATE.get(channel_cache_key)
        if backoff_until and backoff_until <= now:
            backend_main.YOUTUBE_UPLOAD_FAILURE_STATE.pop(channel_cache_key, None)
            backoff_until = None

        cache_entry = backend_main.YOUTUBE_UPLOAD_CACHE.get(channel_cache_key)
        if cache_entry:
            cached_timestamp, cached_videos = cache_entry
        else:
            cached_timestamp, cached_videos = backend_main._load_cached_uploads(channel_cache_key)
            if cached_videos:
                backend_main.YOUTUBE_UPLOAD_CACHE[channel_cache_key] = (cached_timestamp or now, cached_videos)

        videos: List[ThumbnailChannelVideoResponse] = []
        cache_is_fresh = False
        if cached_timestamp and (now - cached_timestamp) < backend_main.YOUTUBE_UPLOAD_CACHE_TTL:
            videos = list(cached_videos)
            cache_is_fresh = True

        backoff_active = bool(backoff_until and backoff_until > now)
        should_refresh = (
            not cache_is_fresh
            and backend_main.YOUTUBE_CLIENT
            and youtube_meta.get("channel_id")
            and remaining_refresh_budget > 0
            and not backoff_active
        )

        if should_refresh:
            try:
                uploads = backend_main.YOUTUBE_CLIENT.fetch_recent_uploads(youtube_meta["channel_id"], max_results=6)

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
                backend_main.YOUTUBE_UPLOAD_CACHE[channel_cache_key] = (fetched_at, videos)
                backend_main._save_cached_uploads(channel_cache_key, fetched_at, videos)
                cache_is_fresh = True
                remaining_refresh_budget = max(0, remaining_refresh_budget - 1)
            except backend_main.YouTubeDataAPIError as exc:  # pragma: no cover - API failure
                backend_main.logger.warning("Failed to fetch YouTube uploads for %s: %s", channel_code, exc)
                remaining_refresh_budget = max(0, remaining_refresh_budget - 1)
                error_message = str(exc).lower()
                if "quota" in error_message or "useratelimitexceeded" in error_message:
                    backend_main.YOUTUBE_UPLOAD_FAILURE_STATE[channel_cache_key] = now + backend_main.YOUTUBE_UPLOAD_BACKOFF
                if cached_videos:
                    videos = list(cached_videos)
        elif backoff_active:
            backend_main.logger.info(
                "Skipping YouTube refresh for %s due to quota backoff until %s",
                channel_code,
                backoff_until.isoformat() if backoff_until else "?",
            )
        elif remaining_refresh_budget <= 0 and not cache_is_fresh:
            backend_main.logger.debug("Refresh budget exhausted for thumbnails; using cached entries for %s", channel_code)

        if not videos and cached_videos:
            videos = list(cached_videos)

        entry["videos"] = videos

    overview_channels: List[ThumbnailChannelBlockResponse] = []

    for channel_code, entry in sorted(channel_map.items()):
        primary_library = backend_main._channel_primary_library_dir(channel_code)
        if primary_library.exists():
            try:
                library_path = str(primary_library.relative_to(backend_main.PROJECT_ROOT))
            except ValueError:
                library_path = str(primary_library)
        else:
            library_path = None
        if channel_code not in merged_channels:
            backend_main._merge_disk_thumbnail_variants(channel_code, entry)
            merged_channels.add(channel_code)
        summary_obj = entry.get("summary")
        if summary_obj is None:
            summary_obj = ThumbnailChannelSummaryResponse(
                total=len(entry.get("projects", [])),
            )
        overview_channels.append(
            ThumbnailChannelBlockResponse(
                channel=channel_code,
                channel_title=backend_main._resolve_channel_title(channel_code, channel_info_map),
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


@router.patch("/{channel}/{video}", response_model=Dict[str, str])
def update_thumbnail_project(channel: str, video: str, payload: ThumbnailProjectUpdateRequest):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return {"status": "ok"}
    now = datetime.now(timezone.utc).isoformat()
    with backend_main.THUMBNAIL_PROJECTS_LOCK:
        path, document = backend_main._load_thumbnail_projects_document()
        project = backend_main._get_or_create_thumbnail_project(document, channel_code, video_number)

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
            normalized_tags = backend_main._normalize_thumbnail_tags(updates.get("tags"))
            if normalized_tags:
                project["tags"] = normalized_tags
            else:
                project.pop("tags", None)

        if "status" in updates:
            project["status"] = backend_main._normalize_thumbnail_status(updates.get("status"))
            project["status_updated_at"] = now

        if "selected_variant_id" in updates:
            variant_id = updates.get("selected_variant_id")
            if variant_id:
                project["selected_variant_id"] = variant_id
            else:
                project.pop("selected_variant_id", None)

        project["updated_at"] = now
        backend_main._write_thumbnail_projects_document(path, document)
    return {"status": "ok"}


@router.get(
    "/{channel}/{video}/thumb-spec",
    response_model=ThumbnailThumbSpecResponse,
)
def get_thumbnail_thumb_spec(
    channel: str,
    video: str,
    stable: Optional[str] = Query(None, description="stable output id (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    from backend import main as backend_main

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

    stable_id = backend_main._normalize_thumbnail_stable_id(stable if stable is not None else variant)
    loaded = None
    stable_exists = False
    stable_path = None
    if stable_id:
        stable_path = backend_main._thumb_spec_stable_path(channel_code, video_number, stable_id)
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


@router.put(
    "/{channel}/{video}/thumb-spec",
    response_model=ThumbnailThumbSpecResponse,
)
def upsert_thumbnail_thumb_spec(
    channel: str,
    video: str,
    request: ThumbnailThumbSpecUpdateRequest,
    stable: Optional[str] = Query(None, description="stable output id (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    try:
        from script_pipeline.thumbnails.thumb_spec import THUMB_SPEC_SCHEMA_V1, save_thumb_spec, validate_thumb_spec_payload
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"thumb_spec module is not available: {exc}") from exc

    overrides = request.overrides if isinstance(request.overrides, dict) else {}
    try:
        stable_id = backend_main._normalize_thumbnail_stable_id(stable if stable is not None else variant)
        if not stable_id:
            save_thumb_spec(channel_code, video_number, overrides)
        else:
            path = backend_main._thumb_spec_stable_path(channel_code, video_number, stable_id)
            payload = {
                "schema": THUMB_SPEC_SCHEMA_V1,
                "channel": channel_code,
                "video": video_number,
                "overrides": overrides,
                "updated_at": backend_main._utc_now_iso_z(),
            }
            validated = validate_thumb_spec_payload(payload, channel=channel_code, video=video_number)
            write_payload = {
                "schema": THUMB_SPEC_SCHEMA_V1,
                "channel": channel_code,
                "video": video_number,
                "overrides": overrides,
                "updated_at": validated.get("updated_at") or backend_main._utc_now_iso_z(),
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

    stable_id = backend_main._normalize_thumbnail_stable_id(stable if stable is not None else variant)
    if stable_id:
        return get_thumbnail_thumb_spec(channel_code, video_number, stable=stable_id)
    return get_thumbnail_thumb_spec(channel_code, video_number)


@router.get(
    "/{channel}/{video}/text-line-spec",
    response_model=ThumbnailTextLineSpecResponse,
)
def get_thumbnail_text_line_spec(
    channel: str,
    video: str,
    stable: Optional[str] = Query(None, description="stable output id (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    stable_raw = stable if stable is not None else variant
    stable_id = backend_main._normalize_thumbnail_stable_id(stable_raw) if stable_raw else None
    stable_label = stable_id or "default"
    legacy_path = backend_main.THUMBNAIL_ASSETS_DIR / channel_code / video_number / "text_line_spec.json"
    stable_path = (
        backend_main._text_line_spec_stable_path(channel_code, video_number, stable_id) if stable_id else None
    )
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


@router.put(
    "/{channel}/{video}/text-line-spec",
    response_model=ThumbnailTextLineSpecResponse,
)
def upsert_thumbnail_text_line_spec(
    channel: str,
    video: str,
    request: ThumbnailTextLineSpecUpdateRequest,
    stable: Optional[str] = Query(None, description="stable output id (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    stable_raw = stable if stable is not None else variant
    stable_id = backend_main._normalize_thumbnail_stable_id(stable_raw) if stable_raw else None
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
        "schema": backend_main.THUMBNAIL_TEXT_LINE_SPEC_SCHEMA_V1,
        "channel": channel_code,
        "video": video_number,
        "stable": stable_label,
        "lines": lines_out,
        "updated_at": backend_main._utc_now_iso_z(),
    }
    path = (
        backend_main._text_line_spec_stable_path(channel_code, video_number, stable_id)
        if stable_id
        else (backend_main.THUMBNAIL_ASSETS_DIR / channel_code / video_number / "text_line_spec.json")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    if stable_id:
        return get_thumbnail_text_line_spec(channel_code, video_number, stable=stable_id)
    return get_thumbnail_text_line_spec(channel_code, video_number, stable="")


@router.get(
    "/{channel}/{video}/elements-spec",
    response_model=ThumbnailElementsSpecResponse,
)
def get_thumbnail_elements_spec(
    channel: str,
    video: str,
    stable: Optional[str] = Query(None, description="stable output id (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    stable_raw = stable if stable is not None else variant
    stable_id = backend_main._normalize_thumbnail_stable_id(stable_raw) if stable_raw else None
    stable_label = stable_id or "default"
    legacy_path = backend_main.THUMBNAIL_ASSETS_DIR / channel_code / video_number / "elements_spec.json"
    stable_path = (
        backend_main._elements_spec_stable_path(channel_code, video_number, stable_id) if stable_id else None
    )
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


@router.put(
    "/{channel}/{video}/elements-spec",
    response_model=ThumbnailElementsSpecResponse,
)
def upsert_thumbnail_elements_spec(
    channel: str,
    video: str,
    request: ThumbnailElementsSpecUpdateRequest,
    stable: Optional[str] = Query(None, description="stable output id (e.g. 00_thumb_1)"),
    variant: Optional[str] = Query(None, description="alias of stable (deprecated)"),
):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    stable_raw = stable if stable is not None else variant
    stable_id = backend_main._normalize_thumbnail_stable_id(stable_raw) if stable_raw else None
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
        "schema": backend_main.THUMBNAIL_ELEMENTS_SPEC_SCHEMA_V1,
        "channel": channel_code,
        "video": video_number,
        "stable": stable_label,
        "elements": elements_out,
        "updated_at": backend_main._utc_now_iso_z(),
    }
    path = (
        backend_main._elements_spec_stable_path(channel_code, video_number, stable_id)
        if stable_id
        else (backend_main.THUMBNAIL_ASSETS_DIR / channel_code / video_number / "elements_spec.json")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    if stable_id:
        return get_thumbnail_elements_spec(channel_code, video_number, stable=stable_id)
    return get_thumbnail_elements_spec(channel_code, video_number, stable="")


@router.get("/{channel}/download.zip")
def download_thumbnail_zip(
    channel: str,
    mode: str = Query("selected", description="selected | all | two_up"),
):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel)
    mode_norm = (mode or "selected").strip().lower()
    if mode_norm not in {"selected", "all", "two_up"}:
        raise HTTPException(status_code=400, detail="mode must be 'selected', 'all', or 'two_up'")

    projects_path = backend_main._resolve_thumbnail_projects_path()
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
        video_number = backend_main._coerce_video_from_dir(str(raw_project.get("video") or ""))
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

            candidate = (backend_main.THUMBNAIL_ASSETS_DIR / rel).resolve()
            try:
                candidate.relative_to(backend_main.THUMBNAIL_ASSETS_DIR.resolve())
            except (OSError, ValueError):
                continue
            if not candidate.is_file():
                continue

            if mode_norm == "two_up":
                safe_variant = candidate.stem
            else:
                variant_id = str(raw_variant.get("id") or "").strip() or "variant"
                safe_variant = re.sub(r"[^0-9A-Za-zぁ-んァ-ン一-龥ー_-]+", "_", variant_id).strip("_") or "variant"
            suffix = candidate.suffix.lower() or ".png"
            arcname = f"{video_number}/{safe_variant}{suffix}"
            if arcname in used_names:
                arcname = f"{video_number}/{safe_variant}_{uuid.uuid4().hex[:6]}{suffix}"
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


@router.post(
    "/{channel}/library/upload",
    response_model=List[ThumbnailLibraryAssetResponse],
)
async def upload_thumbnail_library_assets(channel: str, files: List[UploadFile] = File(...)):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel)
    if not files:
        raise HTTPException(status_code=400, detail="アップロードする画像を選択してください。")
    library_dir = backend_main._channel_primary_library_dir(channel_code, ensure=True)
    assets: List[ThumbnailLibraryAssetResponse] = []
    for file in files:
        if not file.filename:
            continue
        sanitized = backend_main._sanitize_library_filename(file.filename, default_prefix="library_asset")
        destination = backend_main._ensure_unique_filename(library_dir, sanitized)
        await backend_main._save_upload_file(file, destination)
        assets.append(backend_main._build_library_asset_response(channel_code, destination, base_dir=library_dir))
    if not assets:
        raise HTTPException(status_code=400, detail="有効な画像ファイルがありませんでした。")
    return assets


@router.post(
    "/{channel}/library/import",
    response_model=ThumbnailLibraryAssetResponse,
)
def import_thumbnail_library_asset(channel: str, payload: ThumbnailLibraryImportRequest):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel)
    library_dir = backend_main._channel_primary_library_dir(channel_code, ensure=True)
    source_url = payload.url.strip()
    if not source_url:
        raise HTTPException(status_code=400, detail="URL を指定してください。")
    try:
        response = requests.get(source_url, timeout=backend_main.THUMBNAIL_REMOTE_FETCH_TIMEOUT)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"画像の取得に失敗しました: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"画像の取得に失敗しました (status {response.status_code})")
    content = response.content
    if not content:
        raise HTTPException(status_code=400, detail="画像データが空です。")
    if len(content) > backend_main.THUMBNAIL_LIBRARY_MAX_BYTES:
        raise HTTPException(status_code=400, detail="画像サイズが大きすぎます。")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    suffix = mimetypes.guess_extension(content_type) if content_type else None
    if suffix == ".jpe":
        suffix = ".jpg"
    if suffix not in backend_main.THUMBNAIL_SUPPORTED_EXTENSIONS:
        suffix = None
    candidate_name = payload.file_name.strip() if payload.file_name else ""
    if not candidate_name:
        parsed = urllib.parse.urlparse(source_url)
        candidate_name = Path(parsed.path).name or ""
    if candidate_name:
        sanitized = backend_main._sanitize_library_filename(candidate_name, default_prefix="imported")
        if suffix and not sanitized.lower().endswith(suffix):
            sanitized = f"{Path(sanitized).stem}{suffix}"
    else:
        sanitized = backend_main._sanitize_library_filename(f"imported{suffix or '.png'}", default_prefix="imported")
    destination = backend_main._ensure_unique_filename(library_dir, sanitized)
    try:
        with destination.open("wb") as buffer:
            buffer.write(content)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"画像の保存に失敗しました: {exc}") from exc
    return backend_main._build_library_asset_response(channel_code, destination, base_dir=library_dir)


@router.get(
    "/{channel}/library",
    response_model=List[ThumbnailLibraryAssetResponse],
)
def get_thumbnail_library(channel: str):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel)
    return backend_main._list_channel_thumbnail_library(channel_code)


@router.patch(
    "/{channel}/library/{asset_name}",
    response_model=ThumbnailLibraryAssetResponse,
)
def rename_thumbnail_library_asset(
    channel: str,
    asset_name: str,
    payload: ThumbnailLibraryRenameRequest,
):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel)
    base_dir, current_path = backend_main._resolve_library_asset_path(channel_code, asset_name)
    new_name = payload.new_name
    destination = base_dir / new_name
    if destination.exists():
        raise HTTPException(status_code=409, detail="同名のファイルが既に存在します。")
    try:
        current_path.rename(destination)
    except OSError as exc:  # pragma: no cover - filesystem failure
        raise HTTPException(status_code=500, detail=f"ファイル名の変更に失敗しました: {exc}") from exc
    return backend_main._build_library_asset_response(channel_code, destination)


@router.delete(
    "/{channel}/library/{asset_path:path}",
    status_code=204,
    response_class=PlainTextResponse,
)
def delete_thumbnail_library_asset(channel: str, asset_path: str):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel)
    _, source_path = backend_main._resolve_library_asset_path(channel_code, asset_path)
    try:
        source_path.unlink()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="asset not found")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"ファイルの削除に失敗しました: {exc}") from exc
    return PlainTextResponse("", status_code=204)


@router.post(
    "/{channel}/library/{asset_name}/assign",
    response_model=ThumbnailLibraryAssignResponse,
)
def assign_thumbnail_library_asset(
    channel: str,
    asset_name: str,
    payload: ThumbnailLibraryAssignRequest,
):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(payload.video)
    _, source_path = backend_main._resolve_library_asset_path(channel_code, asset_name)
    image_path, public_url = backend_main._copy_library_asset_to_video(channel_code, video_number, source_path)
    label = payload.label.strip() if payload.label else Path(source_path.name).stem
    backend_main._persist_thumbnail_variant(
        channel_code,
        video_number,
        label=label,
        status="draft",
        image_path=image_path,
        make_selected=bool(payload.make_selected),
    )
    backend_main._append_thumbnail_quick_history(
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


@router.get("/history", response_model=List[ThumbnailQuickHistoryEntry])
def get_thumbnail_quick_history(
    channel: Optional[str] = Query(None, description="CHコード（例: CH06）"),
    limit: int = Query(20, ge=1, le=200),
):
    from backend import main as backend_main

    channel_code = normalize_channel_code(channel) if channel else None
    return backend_main._read_thumbnail_quick_history(channel_code, limit)


@router.post(
    "/{channel}/library/{asset_name}/describe",
    response_model=ThumbnailDescriptionResponse,
)
def describe_thumbnail_library_asset(channel: str, asset_name: str):
    raise HTTPException(
        status_code=400,
        detail="thumbnail library describe is disabled: LLM API is not used for thumbnails",
    )
