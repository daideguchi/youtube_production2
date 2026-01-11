from __future__ import annotations

from typing import List

from fastapi import APIRouter

from backend.app.llm_models import LlmModelInfo, load_llm_model_scores

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.get("/models", response_model=List[LlmModelInfo])
def list_llm_models() -> List[LlmModelInfo]:
    return load_llm_model_scores()

