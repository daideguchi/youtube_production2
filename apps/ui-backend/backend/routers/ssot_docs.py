from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import List, Tuple

from fastapi import APIRouter, HTTPException

from backend.app.channels_models import (
    PersonaDocumentResponse,
    PersonaDocumentUpdateRequest,
    PlanningTemplateResponse,
    PlanningTemplateUpdateRequest,
)
from backend.app.normalize import normalize_channel_code
from backend.app.prompts_store import safe_relative_path, write_text_with_lock
from factory_common.paths import persona_path as ssot_persona_path
from factory_common.paths import planning_root as ssot_planning_root
from script_pipeline.tools import planning_requirements

router = APIRouter(prefix="/api/ssot", tags=["ssot"])


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
