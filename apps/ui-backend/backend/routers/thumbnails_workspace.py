from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.main import (
    ThumbnailChannelBlockResponse,
    ThumbnailChannelSummaryResponse,
    ThumbnailChannelVideoResponse,
    ThumbnailDescriptionResponse,
    ThumbnailOverviewResponse,
    ThumbnailProjectResponse,
    ThumbnailProjectUpdateRequest,
    ThumbnailQuickHistoryEntry,
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
