from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app import lock_store

router = APIRouter(prefix="/api/admin", tags=["admin"])


class LockMetricSample(BaseModel):
    timestamp: str
    type: str
    timeout: int
    unexpected: int


class LockMetricsDailySummary(BaseModel):
    date: str
    timeout: int
    unexpected: int


class LockMetricsResponse(BaseModel):
    timeout: int
    unexpected: int
    history: List[LockMetricSample]
    daily: List["LockMetricsDailySummary"]


LockMetricsResponse.model_rebuild()


@router.get("/lock-metrics", response_model=LockMetricsResponse)
def get_lock_metrics() -> LockMetricsResponse:
    history = [LockMetricSample(**entry) for entry in lock_store.LOCK_HISTORY]
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with sqlite3.connect(lock_store.LOCK_DB_PATH) as conn:
        aggregates = conn.execute(
            """
            SELECT substr(occurred_at, 1, 10) AS day,
                   SUM(CASE WHEN event_type = 'timeout' THEN 1 ELSE 0 END) AS timeout_count,
                   SUM(CASE WHEN event_type = 'unexpected' THEN 1 ELSE 0 END) AS unexpected_count
            FROM lock_metrics
            WHERE occurred_at >= ?
            GROUP BY day
            ORDER BY day DESC
            LIMIT 7
            """,
            (seven_days_ago,),
        ).fetchall()
    daily = [{"date": row[0], "timeout": row[1], "unexpected": row[2]} for row in aggregates]
    return LockMetricsResponse(
        timeout=lock_store.LOCK_METRICS["timeout"],
        unexpected=lock_store.LOCK_METRICS["unexpected"],
        history=history,
        daily=daily,
    )
