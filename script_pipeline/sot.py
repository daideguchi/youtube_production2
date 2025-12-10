"""
Simple SoT loader/writer for the new script_pipeline.
Keeps status.json under script_pipeline/data/CHXX/NNN/status.json
and does not touch existing pipelines.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "script_pipeline" / "data"


@dataclass
class StageState:
    status: str = "pending"  # pending | processing | completed | failed
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Status:
    script_id: str
    channel: str
    video: str
    metadata: Dict[str, Any]
    stages: Dict[str, StageState]
    status: str = "pending"


def status_path(channel: str, video: str) -> Path:
    return DATA_ROOT / channel / video / "status.json"


def load_status(channel: str, video: str) -> Status:
    path = status_path(channel, video)
    payload = json.loads(path.read_text(encoding="utf-8"))
    stages: Dict[str, StageState] = {}
    for name, info in (payload.get("stages") or {}).items():
        stages[name] = StageState(
            status=info.get("status", "pending"),
            details=info.get("details") or {},
        )
    return Status(
        script_id=payload.get("script_id"),
        channel=payload.get("channel"),
        video=video,
        metadata=payload.get("metadata") or {},
        stages=stages,
        status=payload.get("status", "pending"),
    )


def save_status(st: Status) -> None:
    path = status_path(st.channel, st.video)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "script_id": st.script_id,
        "channel": st.channel,
        "status": st.status,
        "metadata": st.metadata,
        "stages": {k: {"status": v.status, "details": v.details} for k, v in st.stages.items()},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def init_status(channel: str, video: str, title: str, stage_names: List[str]) -> Status:
    script_id = f"{channel}-{video}"
    stages = {name: StageState() for name in stage_names}
    st = Status(
        script_id=script_id,
        channel=channel,
        video=video,
        metadata={"title": title, "expected_title": title},
        stages=stages,
        status="pending",
    )
    save_status(st)
    return st
