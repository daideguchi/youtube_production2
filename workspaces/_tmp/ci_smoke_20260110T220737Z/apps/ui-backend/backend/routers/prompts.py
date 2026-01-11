from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, HTTPException

from backend.app.prompts_store import (
    PromptDocumentResponse,
    PromptDocumentSummaryResponse,
    PromptUpdateRequest,
    build_prompt_document_payload,
    get_prompt_spec,
    load_prompt_documents,
    persist_prompt_document,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


@router.get("", response_model=List[PromptDocumentSummaryResponse])
def list_prompt_documents() -> List[PromptDocumentSummaryResponse]:
    specs = sorted(load_prompt_documents().values(), key=lambda item: item.get("label", item["id"]))
    return [PromptDocumentSummaryResponse(**build_prompt_document_payload(spec, include_content=False)) for spec in specs]


@router.get("/{prompt_id}", response_model=PromptDocumentResponse)
def fetch_prompt_document(prompt_id: str) -> PromptDocumentResponse:
    spec = get_prompt_spec(prompt_id)
    payload = build_prompt_document_payload(spec, include_content=True)
    return PromptDocumentResponse(**payload)


@router.put("/{prompt_id}", response_model=PromptDocumentResponse)
def update_prompt_document(prompt_id: str, payload: PromptUpdateRequest) -> PromptDocumentResponse:
    spec = get_prompt_spec(prompt_id)
    current = build_prompt_document_payload(spec, include_content=True)
    expected = payload.expected_checksum
    if expected and expected != current["checksum"]:
        raise HTTPException(status_code=409, detail="他のユーザーが先に更新しました。最新内容を読み込み直してください。")
    previous_content: str = current["content"]
    persist_prompt_document(spec, new_content=payload.content, previous_content=previous_content)
    logger.info("Prompt %s updated via UI", prompt_id)
    refreshed = build_prompt_document_payload(spec, include_content=True)
    return PromptDocumentResponse(**refreshed)
