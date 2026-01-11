from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query

from backend.app.channel_info_store import refresh_channel_info
from backend.app.publish_sheet_client import PublishSheetClient, PublishSheetError
from backend.app.publishing_models import (
    PublishingScheduleChannelSummary,
    PublishingScheduleOverviewResponse,
    PublishingScheduleVideoItem,
)
from backend.main import (
    _channel_sort_key,
    list_known_channel_codes,
    normalize_optional_text,
    normalize_planning_video_number,
    parse_iso_datetime,
)

router = APIRouter(prefix="/api/publishing", tags=["publishing"])


@router.get("/runway", response_model=PublishingScheduleOverviewResponse)
def publishing_runway_overview(
    refresh: bool = Query(False, description="キャッシュを無視して外部SoTを再取得"),
    limit: int = Query(12, ge=0, le=100, description="各チャンネルで返す今後の予約本数"),
):
    """
    External SoT（Google Sheet）から「投稿予約（公開予約）」の最終到達点を集計する。

    Note:
      - local planning/status の「投稿済みロック」とは別系統。ここは publish sheet を正とする。
      - UI は `schedule_runway_days` をキーに優先度付けできる。
    """

    try:
        client = PublishSheetClient.from_env()
        sheet_rows, fetched_at = client.fetch_rows(force=bool(refresh))
    except PublishSheetError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    tz_name = "Asia/Tokyo"
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone(timedelta(hours=9))

    now_utc = datetime.now(timezone.utc)
    now_jst = now_utc.astimezone(tz)
    today_jst = now_jst.date()

    def _parse_schedule(value: str) -> Optional[datetime]:
        dt = parse_iso_datetime((value or "").strip())
        if not dt:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    warnings: List[str] = []
    grouped: Dict[str, List[Tuple[Optional[datetime], PublishingScheduleVideoItem]]] = {}

    for row in sheet_rows:
        ch_raw = str(row.get("Channel") or "").strip()
        ch = ch_raw.upper()
        if not ch or not re.match(r"^CH\d+$", ch):
            if ch_raw:
                warnings.append(f"Invalid Channel in publish sheet: {ch_raw} (row {row.get('_row_number')})")
            continue

        video = normalize_planning_video_number(row.get("VideoNo"))
        scheduled_dt = _parse_schedule(str(row.get("ScheduledPublish (RFC3339)") or ""))

        item = PublishingScheduleVideoItem(
            channel=ch,
            video=video,
            title=normalize_optional_text(row.get("Title")),
            status=normalize_optional_text(row.get("Status")),
            visibility=normalize_optional_text(row.get("Visibility")),
            scheduled_publish_at=scheduled_dt.astimezone(tz).isoformat() if scheduled_dt else None,
            youtube_video_id=normalize_optional_text(row.get("YouTube Video ID")),
        )
        grouped.setdefault(ch, []).append((scheduled_dt, item))

    # Ensure local-known channels appear even if the sheet is partial.
    for code in list_known_channel_codes(refresh_channel_info(force=True)):
        grouped.setdefault(code, [])

    summaries: List[PublishingScheduleChannelSummary] = []
    for ch in sorted(grouped.keys(), key=_channel_sort_key):
        pairs = grouped.get(ch) or []
        uploaded = [(dt, it) for dt, it in pairs if (it.youtube_video_id or "").strip()]
        upcoming = [(dt, it) for dt, it in uploaded if dt is not None and dt > now_utc]
        published = [(dt, it) for dt, it in uploaded if dt is not None and dt <= now_utc]

        upcoming.sort(key=lambda x: x[0])
        published.sort(key=lambda x: x[0])

        last_scheduled_date = None
        runway_days = 0
        if upcoming:
            last_scheduled_dt = upcoming[-1][0].astimezone(tz)
            last_scheduled_date = last_scheduled_dt.date().isoformat()
            runway_days = (last_scheduled_dt.date() - today_jst).days
            if runway_days < 0:
                runway_days = 0

        last_published_date = None
        if published:
            last_published_dt = published[-1][0].astimezone(tz)
            last_published_date = last_published_dt.date().isoformat()

        summaries.append(
            PublishingScheduleChannelSummary(
                channel=ch,
                last_published_date=last_published_date,
                last_scheduled_date=last_scheduled_date,
                schedule_runway_days=runway_days,
                upcoming_count=len(upcoming),
                upcoming=[it for _, it in upcoming[: int(limit)]],
            )
        )

    # Sort by runway (short first), then channel.
    summaries.sort(key=lambda s: (s.schedule_runway_days, _channel_sort_key(s.channel)))

    return PublishingScheduleOverviewResponse(
        status="ok",
        timezone=tz_name,
        today=today_jst.isoformat(),
        now=now_jst.isoformat(),
        sheet_id=client.config.sheet_id,
        sheet_name=client.config.sheet_name,
        fetched_at=fetched_at,
        channels=summaries,
        warnings=warnings[:200],
    )
