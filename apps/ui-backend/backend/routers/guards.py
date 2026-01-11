from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from backend.app.workflow_precheck_models import (
    WorkflowPrecheckItem,
    WorkflowPrecheckPendingSummary,
    WorkflowPrecheckReadyEntry,
    WorkflowPrecheckResponse,
)
from backend.core.tools import workflow_precheck as workflow_precheck_tools

router = APIRouter(prefix="/api/guards", tags=["guards"])


@router.get("/workflow-precheck", response_model=WorkflowPrecheckResponse)
def workflow_precheck_summary(
    channel: Optional[str] = Query(None, description="CHコードで絞り込み"),
    limit: int = Query(5, ge=1, le=50, description="各チャンネルで返す pending アイテム数"),
) -> WorkflowPrecheckResponse:
    channel_filter = channel.upper() if channel else None
    pending_summaries = workflow_precheck_tools.gather_pending(
        channel_codes=[channel_filter] if channel_filter else None,
        limit=limit,
    )
    ready_entries = workflow_precheck_tools.collect_ready_for_audio(channel_code=channel_filter)

    def _pick(row: Dict[str, Any], *keys: str) -> Optional[str]:
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    pending_payload: List[WorkflowPrecheckPendingSummary] = []
    for summary in pending_summaries:
        normalized_items: List[WorkflowPrecheckItem] = []
        for row in summary.items:
            video_number = _pick(row, "video_number", "動画番号", "動画ID", "No.") or ""
            script_id = _pick(row, "script_id", "台本番号")
            if not script_id:
                script_id = f"{summary.channel}-{video_number}".rstrip("-") or summary.channel
            normalized_items.append(
                WorkflowPrecheckItem(
                    script_id=script_id,
                    video_number=video_number,
                    progress=_pick(row, "progress", "進捗"),
                    title=_pick(row, "title", "タイトル"),
                    flag=_pick(row, "flag", "creation_flag", "作成フラグ"),
                )
            )
        pending_payload.append(
            WorkflowPrecheckPendingSummary(
                channel=summary.channel,
                count=summary.count,
                items=normalized_items,
            )
        )

    ready_payload = [
        WorkflowPrecheckReadyEntry(
            channel=item.channel,
            video_number=item.video_number,
            script_id=item.script_id,
            audio_status=item.audio_status,
        )
        for item in ready_entries
    ]

    return WorkflowPrecheckResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        pending=pending_payload,
        ready=ready_payload,
    )

