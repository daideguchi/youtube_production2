from __future__ import annotations

"""
Prompt documents store (UI backend internal).

SoT:
- packages/script_pipeline/prompts/** (shared prompts/templates)
- packages/script_pipeline/channels/**/script_prompt.txt (channel prompts)

created: 2026-01-09
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.app.channel_info_store import CHANNELS_DIR
from backend.app.path_utils import safe_relative_path
from backend.core.portalocker_compat import portalocker
from factory_common.paths import script_pkg_root

logger = logging.getLogger(__name__)

SCRIPT_PIPELINE_ROOT = script_pkg_root()
SCRIPT_PIPELINE_PROMPTS_ROOT = SCRIPT_PIPELINE_ROOT / "prompts"
PROMPT_TEMPLATES_ROOT = SCRIPT_PIPELINE_PROMPTS_ROOT / "templates"

LOCK_TIMEOUT_SECONDS = 5.0

def _relative_prompt_path(path: Path) -> str:
    rel = safe_relative_path(path)
    return rel if rel else str(path)


def _prompt_checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid JSON: {path}") from exc


def write_text_with_lock(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with portalocker.Lock(
            str(path),
            mode="w",
            encoding="utf-8",
            timeout=LOCK_TIMEOUT_SECONDS,
        ) as handle:
            handle.write(content)
            handle.flush()
    except portalocker.exceptions.Timeout as exc:
        logger.warning("Lock timeout while writing %s", path)
        raise HTTPException(
            status_code=423,
            detail="ファイルが使用中です。数秒後に再試行してください。",
        ) from exc
    except portalocker.exceptions.LockException as exc:
        logger.exception("Unexpected lock error for %s", path)
        raise HTTPException(
            status_code=500,
            detail="ファイルの更新中に予期しないロックエラーが発生しました。",
        ) from exc


def write_json(path: Path, payload: dict) -> None:
    write_text_with_lock(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


class PromptSyncTargetResponse(BaseModel):
    path: str
    exists: bool
    checksum: Optional[str] = None
    updated_at: Optional[str] = None


class PromptDocumentSummaryResponse(BaseModel):
    id: str
    label: str
    description: Optional[str] = None
    relative_path: str
    size_bytes: int
    updated_at: Optional[str] = None
    checksum: str
    sync_targets: List[PromptSyncTargetResponse] = Field(default_factory=list)


class PromptDocumentResponse(PromptDocumentSummaryResponse):
    content: str


class PromptUpdateRequest(BaseModel):
    content: str
    expected_checksum: Optional[str] = Field(
        default=None,
        description="前回取得時のチェックサム。整合性チェックに使用する。",
    )

    @field_validator("content")
    @classmethod
    def ensure_string(cls, value: str) -> str:
        if value is None:
            raise ValueError("content is required")
        return value


def _prompt_spec(
    prompt_id: str,
    label: str,
    primary_path: Path,
    *,
    description: Optional[str] = None,
    sync_paths: Optional[list[Path]] = None,
    channel_code: Optional[str] = None,
    channel_info_path: Optional[Path] = None,
) -> Dict[str, Any]:
    return {
        "id": prompt_id,
        "label": label,
        "description": description,
        "primary_path": primary_path,
        "sync_paths": sync_paths or [],
        "channel_code": channel_code,
        "channel_info_path": channel_info_path,
    }


def _discover_template_prompt_specs() -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    if not PROMPT_TEMPLATES_ROOT.exists():
        return specs
    for path in sorted(PROMPT_TEMPLATES_ROOT.glob("*.txt")):
        stem = path.stem
        specs.append(
            _prompt_spec(
                prompt_id=f"template_{stem}",
                label=f"テンプレート {stem}",
                description=f"{stem} 用の台本テンプレート",
                primary_path=path,
            )
        )
    return specs


def _discover_script_pipeline_prompt_specs() -> List[Dict[str, Any]]:
    """
    Discover non-template script_pipeline prompt files.

    Notes:
    - Prompt Manager is a human-facing tool; missing prompts cause "where is the real prompt?"
      confusion, especially in multi-agent setups.
    - We keep curated base specs (stable IDs/labels) but also expose the rest as auto-discovered
      entries to avoid "hidden prompts".
    """

    specs: List[Dict[str, Any]] = []
    if not SCRIPT_PIPELINE_PROMPTS_ROOT.exists():
        return specs

    # Avoid duplicates: these are already exposed as curated base specs with stable IDs/labels.
    curated = {
        "youtube_description_prompt.txt",
        "phase2_audio_prompt.txt",
        "llm_polish_template.txt",
        "orchestrator_prompt.txt",
        "chapter_enhancement_prompt.txt",
        "init.txt",
    }
    for path in sorted(SCRIPT_PIPELINE_PROMPTS_ROOT.glob("*.txt")):
        if path.name in curated:
            continue
        stem = path.stem
        specs.append(
            _prompt_spec(
                prompt_id=f"script_pipeline_prompt_{stem}",
                label=f"script_pipeline {stem}",
                description="script_pipeline prompt (auto-discovered)",
                primary_path=path,
            )
        )
    return specs


def _parse_channel_code(dir_name: str) -> Optional[str]:
    name = dir_name.strip()
    if not name.upper().startswith("CH"):
        return None
    return name.split("-")[0].upper()


def _discover_channel_prompt_specs() -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    if not CHANNELS_DIR.exists():
        return specs
    for entry in sorted(CHANNELS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        channel_code = _parse_channel_code(entry.name)
        if not channel_code:
            continue
        primary = entry / "script_prompt.txt"
        if not primary.exists():
            continue
        specs.append(
            _prompt_spec(
                prompt_id=f"channel_{channel_code.lower()}_script_prompt",
                label=f"{channel_code} script_prompt",
                description="チャンネル固有の台本テンプレート（channel_info.template_path と同期）",
                primary_path=primary,
                channel_code=channel_code,
                channel_info_path=entry / "channel_info.json",
            )
        )
    return specs


def load_prompt_documents() -> Dict[str, Dict[str, Any]]:
    base_specs: List[Dict[str, Any]] = [
        _prompt_spec(
            prompt_id="youtube_description_prompt",
            label="YouTube説明文プロンプト",
            description="SRTから投稿用説明文を生成するテンプレート",
            primary_path=SCRIPT_PIPELINE_PROMPTS_ROOT / "youtube_description_prompt.txt",
        ),
        _prompt_spec(
            prompt_id="phase2_audio_prompt",
            label="台本→音声フェーズプロンプト",
            primary_path=SCRIPT_PIPELINE_PROMPTS_ROOT / "phase2_audio_prompt.txt",
        ),
        _prompt_spec(
            prompt_id="llm_polish_template",
            label="台本ポリッシュプロンプト",
            primary_path=SCRIPT_PIPELINE_PROMPTS_ROOT / "llm_polish_template.txt",
        ),
        _prompt_spec(
            prompt_id="orchestrator_prompt",
            label="オーケストレータプロンプト",
            primary_path=SCRIPT_PIPELINE_PROMPTS_ROOT / "orchestrator_prompt.txt",
        ),
        _prompt_spec(
            prompt_id="chapter_enhancement_prompt",
            label="章エンハンスプロンプト",
            primary_path=SCRIPT_PIPELINE_PROMPTS_ROOT / "chapter_enhancement_prompt.txt",
        ),
        _prompt_spec(
            prompt_id="init_prompt",
            label="初期化プロンプト (init)",
            primary_path=SCRIPT_PIPELINE_PROMPTS_ROOT / "init.txt",
        ),
    ]
    script_pipeline_specs = _discover_script_pipeline_prompt_specs()
    template_specs = _discover_template_prompt_specs()
    channel_specs = _discover_channel_prompt_specs()
    merged: Dict[str, Dict[str, Any]] = {}
    for spec in [*base_specs, *script_pipeline_specs, *template_specs, *channel_specs]:
        merged[spec["id"]] = spec
    return merged


def get_prompt_spec(prompt_id: str) -> Dict[str, Any]:
    spec = load_prompt_documents().get(prompt_id)
    if not spec:
        raise HTTPException(status_code=404, detail="指定したプロンプトは登録されていません。")
    return spec


def describe_prompt_sync_target(path: Path) -> PromptSyncTargetResponse:
    rel_path = _relative_prompt_path(path)
    if not path.exists():
        return PromptSyncTargetResponse(path=rel_path, exists=False)
    try:
        stat = path.stat()
        content = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem failure
        logger.exception("Failed to read prompt sync target %s: %s", path, exc)
        raise HTTPException(status_code=500, detail=f"{rel_path} の読み込みに失敗しました。") from exc
    return PromptSyncTargetResponse(
        path=rel_path,
        exists=True,
        checksum=_prompt_checksum(content),
        updated_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def build_prompt_document_payload(spec: Dict[str, Any], *, include_content: bool) -> Dict[str, Any]:
    primary_path: Path = spec["primary_path"]
    rel_path = _relative_prompt_path(primary_path)
    if not primary_path.exists():
        raise HTTPException(status_code=404, detail=f"{spec.get('label', spec['id'])} が見つかりません: {rel_path}")
    try:
        stat = primary_path.stat()
        content = primary_path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem failure
        logger.exception("Failed to read prompt %s: %s", primary_path, exc)
        raise HTTPException(status_code=500, detail=f"{rel_path} の読み込みに失敗しました。") from exc
    checksum = _prompt_checksum(content)
    sync_targets = []
    for sync_path in spec.get("sync_paths", []) or []:
        if sync_path == primary_path:
            continue
        sync_targets.append(describe_prompt_sync_target(sync_path))
    payload: Dict[str, Any] = {
        "id": spec["id"],
        "label": spec.get("label", spec["id"]),
        "description": spec.get("description"),
        "relative_path": rel_path,
        "size_bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "checksum": checksum,
        "sync_targets": sync_targets,
    }
    if include_content:
        payload["content"] = content
    return payload


def persist_prompt_document(spec: Dict[str, Any], *, new_content: str, previous_content: str) -> None:
    unique_paths: List[Path] = []
    for path in [spec["primary_path"], *(spec.get("sync_paths", []) or [])]:
        if path not in unique_paths:
            unique_paths.append(path)
    updated_paths: List[Path] = []
    try:
        for path in unique_paths:
            write_text_with_lock(path, new_content)
            updated_paths.append(path)
        # channel_info.json へも反映（script_prompt フィールド）
        channel_info_path = spec.get("channel_info_path")
        if channel_info_path:
            if channel_info_path.exists():
                try:
                    info_payload = load_json(channel_info_path)
                except HTTPException:
                    logger.warning("Failed to load channel_info.json for prompt sync: %s", channel_info_path)
                else:
                    if info_payload.get("script_prompt") != new_content.strip():
                        info_payload["script_prompt"] = new_content.strip()
                        write_json(channel_info_path, info_payload)
            else:
                logger.warning("channel_info.json not found for prompt sync: %s", channel_info_path)
    except HTTPException:
        for path in updated_paths:
            try:
                write_text_with_lock(path, previous_content)
            except HTTPException:
                logger.exception("Failed to roll back prompt file %s", path)
        raise
    except Exception as exc:  # pragma: no cover - unexpected failure
        logger.exception("Unexpected error while updating prompt: %s", exc)
        for path in updated_paths:
            try:
                write_text_with_lock(path, previous_content)
            except HTTPException:
                logger.exception("Failed to roll back prompt file %s", path)
        raise HTTPException(status_code=500, detail="プロンプトの更新に失敗しました。もう一度お試しください。") from exc
