from __future__ import annotations

from fastapi import APIRouter

from backend.app.settings_models import (
    CodexSettingsResponse,
    CodexSettingsUpdate,
    LLMSettingsResponse,
    LLMSettingsUpdate,
)
from backend.main import ChannelImageModelRouting, ImageModelRoutingResponse, ImageModelRoutingUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/llm", response_model=LLMSettingsResponse)
def get_llm_settings() -> LLMSettingsResponse:
    from backend.main import get_llm_settings as impl

    return impl()


@router.put("/llm", response_model=LLMSettingsResponse)
def update_llm_settings(payload: LLMSettingsUpdate) -> LLMSettingsResponse:
    from backend.main import update_llm_settings as impl

    return impl(payload)


@router.get("/codex", response_model=CodexSettingsResponse)
def get_codex_settings() -> CodexSettingsResponse:
    from backend.main import get_codex_settings as impl

    return impl()


@router.put("/codex", response_model=CodexSettingsResponse)
def update_codex_settings(payload: CodexSettingsUpdate) -> CodexSettingsResponse:
    from backend.main import update_codex_settings as impl

    return impl(payload)


@router.get("/image-model-routing", response_model=ImageModelRoutingResponse)
def get_image_model_routing() -> ImageModelRoutingResponse:
    from backend.main import get_image_model_routing as impl

    return impl()


@router.patch("/image-model-routing/{channel}", response_model=ChannelImageModelRouting)
def patch_image_model_routing(channel: str, payload: ImageModelRoutingUpdate) -> ChannelImageModelRouting:
    from backend.main import patch_image_model_routing as impl

    return impl(channel, payload)

