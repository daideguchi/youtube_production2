from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import List, Tuple

from fastapi import APIRouter, HTTPException

from backend.app.channel_info_store import find_channel_directory, refresh_channel_info
from backend.app.channels_models import (
    PersonaDocumentResponse,
    PersonaDocumentUpdateRequest,
    PlanningTemplateResponse,
    PlanningTemplateUpdateRequest,
)
from backend.app.prompts_store import safe_relative_path, write_text_with_lock
from factory_common.paths import persona_path as ssot_persona_path
from factory_common.paths import planning_root as ssot_planning_root
from factory_common.paths import repo_root
from factory_common.paths import script_data_root as ssot_script_data_root
from script_pipeline.tools import planning_requirements

router = APIRouter(prefix="/api/ssot", tags=["ssot"])

PROJECT_ROOT = repo_root()
DATA_ROOT = ssot_script_data_root()
CHANNEL_PLANNING_DIR = ssot_planning_root() / "channels"


def normalize_channel_code(channel: str) -> str:
    raw = channel.strip()
    if not raw or Path(raw).name != raw:
        raise HTTPException(status_code=400, detail="Invalid channel identifier")
    channel_code = raw.upper()
    if not re.match(r"^CH\\d+$", channel_code):
        raise HTTPException(status_code=400, detail="Invalid channel identifier")
    if (DATA_ROOT / channel_code).is_dir():
        return channel_code
    if (CHANNEL_PLANNING_DIR / f"{channel_code}.csv").is_file():
        return channel_code
    if find_channel_directory(channel_code) is not None:
        return channel_code
    # Fallback: allow channels known only via channels_info.json cache.
    if channel_code in refresh_channel_info():
        return channel_code
    raise HTTPException(status_code=404, detail=f"Channel {channel_code} not found")


def _persona_doc_path(channel_code: str) -> Path:
    return ssot_persona_path(channel_code)


def _planning_template_path(channel_code: str) -> Path:
    return ssot_planning_root() / "templates" / f"{channel_code}_planning_template.csv"


def _relative_path(path: Path) -> str:
    return str(safe_relative_path(path) or path)


def _collect_required_columns(channel_code: str) -> List[str]:
    specs = planning_requirements.get_channel_requirement_specs(channel_code)
    columns: List[str] = []
    for spec in specs:
        spec_columns = spec.get("required_columns") or []
        for column in spec_columns:
            if column not in columns:
                columns.append(column)
    return columns


def _preview_csv_content(content: str) -> Tuple[List[str], List[str]]:
    stream = io.StringIO(content)
    reader = csv.reader(stream)
    try:
        headers = next(reader)
    except StopIteration as exc:
        raise HTTPException(status_code=400, detail="CSVにヘッダー行がありません。") from exc
    sample = next(reader, [])
    return headers, sample


@router.get("/persona/{channel}", response_model=PersonaDocumentResponse)
def get_persona_document(channel: str):
    channel_code = normalize_channel_code(channel)
    path = _persona_doc_path(channel_code)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{channel_code} のペルソナファイルが見つかりません。")
    content = path.read_text(encoding="utf-8")
    return PersonaDocumentResponse(channel=channel_code, path=_relative_path(path), content=content)


@router.put("/persona/{channel}", response_model=PersonaDocumentResponse)
def update_persona_document(channel: str, payload: PersonaDocumentUpdateRequest):
    channel_code = normalize_channel_code(channel)
    path = _persona_doc_path(channel_code)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{channel_code} のペルソナファイルが見つかりません。")
    content = payload.content
    if not content.strip():
        raise HTTPException(status_code=400, detail="内容を入力してください。")
    if not content.endswith("\n"):
        content += "\n"
    write_text_with_lock(path, content)
    planning_requirements.clear_persona_cache()
    return PersonaDocumentResponse(channel=channel_code, path=_relative_path(path), content=content)


@router.get("/templates/{channel}", response_model=PlanningTemplateResponse)
def get_planning_template(channel: str):
    channel_code = normalize_channel_code(channel)
    path = _planning_template_path(channel_code)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{channel_code} のテンプレートCSVが見つかりません。")
    content = path.read_text(encoding="utf-8")
    headers, sample = _preview_csv_content(content)
    return PlanningTemplateResponse(
        channel=channel_code,
        path=_relative_path(path),
        content=content,
        headers=headers,
        sample=sample,
    )


@router.put("/templates/{channel}", response_model=PlanningTemplateResponse)
def update_planning_template(channel: str, payload: PlanningTemplateUpdateRequest):
    channel_code = normalize_channel_code(channel)
    path = _planning_template_path(channel_code)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{channel_code} のテンプレートCSVが見つかりません。")
    content = payload.content
    headers, sample = _preview_csv_content(content)
    required_columns = _collect_required_columns(channel_code)
    if required_columns:
        missing = [column for column in required_columns if column not in headers]
        if missing:
            joined = ", ".join(missing)
            raise HTTPException(status_code=400, detail=f"テンプレートに必須列が不足しています: {joined}")
    write_text_with_lock(path, content if content.endswith("\n") else content + "\n")
    out_content = content if content.endswith("\n") else content + "\n"
    return PlanningTemplateResponse(
        channel=channel_code,
        path=_relative_path(path),
        content=out_content,
        headers=headers,
        sample=sample,
    )

