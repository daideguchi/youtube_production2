from __future__ import annotations

from typing import Optional

from backend.app.channel_catalog import list_planning_video_numbers, list_video_dirs
from backend.app.channel_info_store import infer_channel_genre
from backend.app.channels_models import ChannelBranding, ChannelSummaryResponse, _resolve_video_workflow


def build_channel_summary(code: str, info: dict) -> ChannelSummaryResponse:
    branding_payload = info.get("branding")
    branding: Optional[ChannelBranding]
    if isinstance(branding_payload, dict):
        try:
            branding = ChannelBranding(**branding_payload)
        except Exception:
            branding = None
    else:
        branding = None
    youtube_info = info.get("youtube") or {}
    branding_info = branding_payload if isinstance(branding_payload, dict) else {}
    planned_video_numbers = list_planning_video_numbers(code)
    video_numbers = set(planned_video_numbers)
    video_numbers.update(video_dir.name for video_dir in list_video_dirs(code))
    return ChannelSummaryResponse(
        code=code,
        name=info.get("name"),
        description=info.get("description"),
        video_count=len(video_numbers),
        branding=branding,
        spreadsheet_id=info.get("spreadsheet_id"),
        youtube_title=(youtube_info.get("title") or info.get("youtube_title")),
        youtube_handle=(
            youtube_info.get("handle")
            or youtube_info.get("custom_url")
            or info.get("youtube_handle")
            or branding_info.get("handle")
        ),
        video_workflow=_resolve_video_workflow(info),
        genre=infer_channel_genre(info),
    )

