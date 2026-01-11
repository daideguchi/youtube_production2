from __future__ import annotations

import json
import logging
from typing import List, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from factory_common import paths as repo_paths

logger = logging.getLogger(__name__)

LLM_MODEL_SCORES_PATH = repo_paths.repo_root() / "ssot" / "HISTORY_llm_model_scores.json"


class LlmMetric(BaseModel):
    name: str
    value: float
    source: Optional[str] = None


class LlmModelInfo(BaseModel):
    id: str
    label: str
    provider: str
    model_id: Optional[str] = None
    iq: int
    knowledge_metric: LlmMetric
    specialist_metric: LlmMetric
    notes: Optional[str] = None
    last_updated: Optional[str] = None


def load_llm_model_scores() -> List[LlmModelInfo]:
    if not LLM_MODEL_SCORES_PATH.exists():
        return []
    try:
        raw_entries = json.loads(LLM_MODEL_SCORES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - configuration error
        logger.error("Failed to parse %s: %s", LLM_MODEL_SCORES_PATH, exc)
        raise HTTPException(status_code=500, detail="LLMモデル情報の読み込みに失敗しました。")
    models: List[LlmModelInfo] = []
    for entry in raw_entries:
        try:
            models.append(LlmModelInfo(**entry))
        except Exception as exc:  # pragma: no cover - validation issue
            logger.warning("Skipping invalid LLM model entry: %s", exc)
    return models

