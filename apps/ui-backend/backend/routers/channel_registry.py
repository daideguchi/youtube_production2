from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException

from backend.app.channel_catalog import CHANNEL_PLANNING_DIR, DATA_ROOT, list_channel_dirs
from backend.app.channel_info_store import refresh_channel_info
from backend.app.channels_models import (
    ChannelAuditItemResponse,
    ChannelProfileResponse,
    ChannelRegisterRequest,
)
from backend.app.normalize import normalize_optional_text
from factory_common.paths import (
    persona_path as ssot_persona_path,
    repo_root as ssot_repo_root,
    research_root as ssot_research_root,
    script_data_root as ssot_script_data_root,
)
from factory_common.youtube_handle import YouTubeHandleResolutionError, normalize_youtube_handle
from script_pipeline.tools import planning_requirements

router = APIRouter(prefix="/api/channels", tags=["channels"])

logger = logging.getLogger(__name__)


def _clean_default_tags(values: Optional[List[str]]) -> Optional[List[str]]:
    if values is None:
        return None
    cleaned: List[str] = []
    for raw in values:
        if raw is None:
            continue
        tag = raw.strip()
        if not tag:
            continue
        if len(tag) > 64:
            raise HTTPException(status_code=400, detail=f"タグが長すぎます: {tag[:32]}…")
        cleaned.append(tag)
    if len(cleaned) > 50:
        raise HTTPException(status_code=400, detail="タグは最大50件までです。")
    return cleaned


@router.post("/register", response_model=ChannelProfileResponse, status_code=201)
def register_channel(payload: ChannelRegisterRequest):
    """
    Create a new channel scaffold from a YouTube handle (deterministic, quota-free).

    This creates:
    - packages/script_pipeline/channels/CHxx-*/channel_info.json + script_prompt.txt
    - workspaces/scripts/CHxx/ (so UI can list the channel)
    - workspaces/planning/channels/CHxx.csv (header-only)
    - workspaces/planning/personas/CHxx_PERSONA.md (stub)
    - configs/sources.yaml entry (planning/persona/prompt + optional targets)
    """

    raw_code = (payload.channel_code or "").strip()
    if not raw_code or Path(raw_code).name != raw_code:
        raise HTTPException(status_code=400, detail="Invalid channel identifier")
    channel_code = raw_code.upper()
    if not re.match(r"^CH\d+$", channel_code):
        raise HTTPException(status_code=400, detail="Invalid channel identifier")
    if (DATA_ROOT / channel_code).exists():
        raise HTTPException(status_code=400, detail=f"Channel {channel_code} already exists")
    try:
        from script_pipeline.tools.channel_registry import create_channel_scaffold

        create_channel_scaffold(
            channel=channel_code,
            name=payload.channel_name,
            youtube_handle=payload.youtube_handle,
            description=payload.description,
            youtube_description=payload.youtube_description,
            default_tags=_clean_default_tags(payload.default_tags),
            benchmarks=(payload.benchmarks.model_dump() if payload.benchmarks is not None else None),
            chapter_count=payload.chapter_count,
            target_chars_min=payload.target_chars_min,
            target_chars_max=payload.target_chars_max,
            overwrite=False,
        )
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except YouTubeHandleResolutionError as exc:
        raise HTTPException(status_code=400, detail=f"YouTubeハンドル解決に失敗しました: {exc}") from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to register channel %s: %s", channel_code, exc)
        raise HTTPException(status_code=500, detail="チャンネル登録に失敗しました。ログを確認してください。") from exc

    planning_requirements.clear_persona_cache()
    refresh_channel_info(force=True)
    from backend.main import _build_channel_profile_response

    return _build_channel_profile_response(channel_code)


@router.get("/audit", response_model=List[ChannelAuditItemResponse])
def audit_channels():
    channel_info_map = refresh_channel_info(force=True)
    out: List[ChannelAuditItemResponse] = []

    def _resolve_sample_path(sample: dict) -> Optional[Path]:
        base = normalize_optional_text(sample.get("base"))
        rel = normalize_optional_text(sample.get("path"))
        if not base or not rel:
            return None
        if base == "research":
            return (ssot_research_root() / rel).resolve()
        if base == "scripts":
            return (ssot_script_data_root() / rel).resolve()
        return None

    for channel_dir in list_channel_dirs():
        code = channel_dir.name.upper()
        info = channel_info_map.get(code, {"channel_id": code})
        youtube_info = info.get("youtube") or {}
        branding_info = info.get("branding") or {}

        youtube_handle_raw = normalize_optional_text(
            youtube_info.get("handle")
            or youtube_info.get("custom_url")
            or branding_info.get("handle")
            or info.get("youtube_handle")
        )
        youtube_handle = None
        if youtube_handle_raw:
            try:
                youtube_handle = normalize_youtube_handle(youtube_handle_raw)
            except Exception:
                youtube_handle = youtube_handle_raw

        youtube_url = normalize_optional_text(youtube_info.get("url") or branding_info.get("url"))
        if not youtube_url and youtube_handle:
            youtube_url = f"https://www.youtube.com/{youtube_handle}"
        avatar_url = normalize_optional_text(branding_info.get("avatar_url"))

        youtube_description = normalize_optional_text(info.get("youtube_description") or youtube_info.get("description"))
        has_youtube_description = bool(youtube_description)
        default_tags = info.get("default_tags") or []
        default_tags_count = len(default_tags) if isinstance(default_tags, list) else 0

        raw_bench = info.get("benchmarks")
        bench_channels_count = 0
        bench_samples_count = 0
        bench_samples: List[dict] = []
        allow_empty_benchmark_channels = False
        if isinstance(raw_bench, dict):
            bench_channels = raw_bench.get("channels") or []
            bench_samples = raw_bench.get("script_samples") or []
            allow_empty_benchmark_channels = bool(raw_bench.get("allow_empty_channels"))
            if isinstance(bench_channels, list):
                bench_channels_count = len(
                    [
                        ch
                        for ch in bench_channels
                        if isinstance(ch, dict) and normalize_optional_text(ch.get("handle") or ch.get("url"))
                    ]
                )
            if isinstance(bench_samples, list):
                bench_samples_count = len(
                    [
                        s
                        for s in bench_samples
                        if isinstance(s, dict)
                        and normalize_optional_text(s.get("base"))
                        and normalize_optional_text(s.get("path"))
                    ]
                )

        issues: List[str] = []
        if not youtube_handle:
            issues.append("missing_youtube_handle")
        if not avatar_url:
            issues.append("missing_avatar_url")
        if not has_youtube_description:
            issues.append("missing_youtube_description")
        if default_tags_count == 0:
            issues.append("missing_default_tags")
        if bench_channels_count == 0 and not allow_empty_benchmark_channels:
            issues.append("missing_benchmark_channels")
        if bench_samples_count == 0:
            issues.append("missing_benchmark_script_samples")

        planning_csv_path = CHANNEL_PLANNING_DIR / f"{code}.csv"
        planning_csv_exists = planning_csv_path.exists()
        planning_rows = 0
        if planning_csv_exists:
            try:
                with planning_csv_path.open("r", encoding="utf-8", newline="") as handle:
                    reader = csv.reader(handle)
                    next(reader, None)
                    planning_rows = sum(1 for _ in reader)
            except Exception:
                planning_rows = 0
        if not planning_csv_exists:
            issues.append("missing_planning_csv")
        elif planning_rows < 30:
            issues.append(f"planning_rows_lt_30:{planning_rows}")

        persona_exists = ssot_persona_path(code).exists()
        if not persona_exists:
            issues.append("missing_persona_doc")

        script_prompt_exists = False
        template_rel = normalize_optional_text(info.get("template_path"))
        if template_rel:
            try:
                script_prompt_exists = (ssot_repo_root() / template_rel).exists()
            except Exception:
                script_prompt_exists = False
        if not script_prompt_exists:
            issues.append("missing_script_prompt")

        if isinstance(bench_samples, list):
            for sample in bench_samples:
                if not isinstance(sample, dict):
                    continue
                resolved = _resolve_sample_path(sample)
                if resolved is None:
                    issues.append("invalid_benchmark_script_sample")
                    continue
                if not resolved.exists():
                    base = normalize_optional_text(sample.get("base")) or "?"
                    rel = normalize_optional_text(sample.get("path")) or "?"
                    issues.append(f"missing_benchmark_script_sample:{base}/{rel}")

        out.append(
            ChannelAuditItemResponse(
                code=code,
                name=normalize_optional_text(info.get("name")),
                youtube_handle=youtube_handle,
                youtube_url=youtube_url,
                avatar_url=avatar_url,
                has_youtube_description=has_youtube_description,
                default_tags_count=default_tags_count,
                benchmark_channels_count=bench_channels_count,
                benchmark_script_samples_count=bench_samples_count,
                planning_rows=planning_rows,
                planning_csv_exists=planning_csv_exists,
                persona_exists=persona_exists,
                script_prompt_exists=script_prompt_exists,
                issues=issues,
            )
        )

    return sorted(out, key=lambda item: item.code)
