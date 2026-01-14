from __future__ import annotations

import copy
import logging
import os
from typing import Dict, List, Optional

from fastapi import HTTPException

from backend.app.llm_catalog_store import _list_openai_model_ids, _list_openrouter_model_ids
from backend.app.settings_models import LLMConfig, LLMSettingsResponse, LLMSettingsUpdate
from backend.app.ui_settings_store import (
    OPENROUTER_API_KEY,
    _get_ui_settings,
    _load_env_value,
    _normalize_llm_settings,
    _validate_provider_endpoint,
    _write_ui_settings,
)

LOGGER_NAME = "ui_backend"
logger = logging.getLogger(LOGGER_NAME)


def get_llm_settings() -> LLMSettingsResponse:
    return _build_llm_settings_response()


def update_llm_settings(payload: LLMSettingsUpdate) -> LLMSettingsResponse:
    settings = _get_ui_settings()
    updated = copy.deepcopy(settings.get("llm", {}))
    if payload.caption_provider:
        updated["caption_provider"] = payload.caption_provider
    if payload.openai_api_key is not None:
        cleaned = payload.openai_api_key.strip() if payload.openai_api_key else ""
        updated["openai_api_key"] = cleaned or None
    if payload.openrouter_api_key is not None:
        cleaned = payload.openrouter_api_key.strip() if payload.openrouter_api_key else ""
        updated["openrouter_api_key"] = cleaned or None
    if payload.openai_caption_model is not None:
        cleaned = payload.openai_caption_model.strip() or None
        if cleaned:
            validation_key = (
                updated.get("openai_api_key") or os.getenv("OPENAI_API_KEY") or _load_env_value("OPENAI_API_KEY")
            )
            if not validation_key:
                raise HTTPException(status_code=400, detail="OpenAI APIキーを先に設定してください。")
            try:
                models = _list_openai_model_ids(validation_key)
            except HTTPException as exc:
                raise HTTPException(status_code=400, detail=f"OpenAI モデル一覧取得に失敗しました: {exc.detail}") from exc
            if cleaned not in models:
                raise HTTPException(status_code=400, detail=f"OpenAIモデル {cleaned} は現在利用できません。")
        updated["openai_caption_model"] = cleaned
    if payload.openrouter_caption_model is not None:
        cleaned = payload.openrouter_caption_model.strip() or None
        if cleaned:
            validation_key = (
                updated.get("openrouter_api_key")
                or os.getenv("OPENROUTER_API_KEY")
                or _load_env_value("OPENROUTER_API_KEY")
            )
            if not validation_key:
                raise HTTPException(status_code=400, detail="OpenRouter APIキーを先に設定してください。")
            try:
                models = _list_openrouter_model_ids(validation_key)
            except HTTPException as exc:
                raise HTTPException(status_code=400, detail=f"OpenRouter モデル一覧取得に失敗しました: {exc.detail}") from exc
            if cleaned not in models:
                raise HTTPException(status_code=400, detail=f"OpenRouterモデル {cleaned} は現在利用できません。")
        updated["openrouter_caption_model"] = cleaned
    if payload.phase_models is not None and isinstance(payload.phase_models, dict):
        merged_phase_models: Dict[str, Dict[str, object]] = copy.deepcopy(updated.get("phase_models") or {})
        for phase_id, info in payload.phase_models.items():
            base = merged_phase_models.get(phase_id, {})
            merged_phase_models[phase_id] = {
                "label": (info.get("label") if isinstance(info, dict) else None) or base.get("label") or phase_id,
                "provider": (info.get("provider") if isinstance(info, dict) else None)
                or base.get("provider")
                or "openrouter",
                "model": (info.get("model") if isinstance(info, dict) else None) or base.get("model"),
            }
        # fail-fast: providerとエンドポイント/キーの整合性を検査
        for _pid, info in merged_phase_models.items():
            prov = str(info.get("provider") or "").lower()
            if prov in {"openai", "openrouter", "gemini"}:
                _validate_provider_endpoint(prov)
        updated["phase_models"] = merged_phase_models
    new_settings = copy.deepcopy(settings)
    new_settings["llm"] = _normalize_llm_settings(updated)
    _write_ui_settings(new_settings)
    return _build_llm_settings_response()


def _build_llm_settings_response() -> LLMSettingsResponse:
    settings = _get_ui_settings()
    llm = settings.get("llm", {})
    openai_env_key = os.getenv("OPENAI_API_KEY") or _load_env_value("OPENAI_API_KEY")
    openrouter_env_key = OPENROUTER_API_KEY or os.getenv("OPENROUTER_API_KEY")
    openai_models: List[str] = []
    openrouter_models: List[str] = []
    openai_models_error: Optional[str] = None
    openrouter_models_error: Optional[str] = None
    try:
        effective_openai_key = llm.get("openai_api_key") or openai_env_key
        if effective_openai_key:
            openai_models = _list_openai_model_ids(effective_openai_key)
            openai_models = sorted(set(openai_models))
    except HTTPException as exc:
        logger.warning("Failed to load OpenAI model list: %s", exc.detail)
        openai_models_error = str(exc.detail)

    def _prioritize_models(model_ids: List[str]) -> List[str]:
        if not model_ids:
            return []
        try:
            from backend.app.llm_models import load_llm_model_scores

            curated = []
            seen = set()
            for model in load_llm_model_scores():
                mid = getattr(model, "model_id", None)
                if mid and mid in model_ids and mid not in seen:
                    curated.append(mid)
                    seen.add(mid)
            for mid in model_ids:
                if mid not in seen:
                    curated.append(mid)
                    seen.add(mid)
            return curated
        except Exception:
            return model_ids

    try:
        effective_openrouter_key = llm.get("openrouter_api_key") or openrouter_env_key
        if effective_openrouter_key:
            openrouter_models = _list_openrouter_model_ids(effective_openrouter_key)
            openrouter_models = _prioritize_models(sorted(set(openrouter_models)))
    except HTTPException as exc:
        logger.warning("Failed to load OpenRouter model list: %s", exc.detail)
        openrouter_models_error = str(exc.detail)

    def _mask_secret(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        trimmed = value.strip()
        if len(trimmed) <= 8:
            return trimmed[:2] + "***"
        return f"{trimmed[:4]}...{trimmed[-4:]}"

    def _export_phase_models() -> Dict[str, Dict[str, object]]:
        exported: Dict[str, Dict[str, object]] = {}
        phase_models = llm.get("phase_models") or {}
        for phase_id, info in phase_models.items():
            exported[phase_id] = {
                "label": info.get("label") or phase_id,
                "provider": info.get("provider") or "openrouter",
                "model": info.get("model"),
            }
        return exported

    def _export_phase_details() -> Dict[str, Dict[str, object]]:
        details: Dict[str, Dict[str, object]] = {}
        # 手動定義: 各フェーズの説明/パス/プロンプト出典
        details.update(
            {
                "caption": {
                    "label": "サムネキャプション",
                    "role": "画像キャプション生成",
                    "path": "apps/ui-backend/backend/main.py::_generate_thumbnail_caption",
                    "prompt_source": "コード内 + configs/llm_router.yaml (tasks.visual_thumbnail_caption)",
                    "endpoint": "LLMRouter (API) + THINK failover",
                },
                "script_rewrite": {
                    "label": "台本リライト",
                    "role": "台本セグメントのリライト",
                    "prompt_source": "SYSTEM_PROMPT + build_user_prompt",
                    "endpoint": "OpenAI(Azure)/OpenRouter",
                },
                "natural_command": {
                    "label": "ナチュラルコマンド",
                    "role": "自然言語コマンド解釈",
                    "path": "apps/ui-backend/backend/main.py::_call_llm_for_command",
                    "prompt_source": "コード内 + configs/llm_router.yaml (tasks.tts_natural_command)",
                    "endpoint": "LLMRouter (API) + THINK failover",
                },
                "research": {
                    "label": "リサーチ",
                    "role": "情報収集・要約",
                    "prompt_source": "メソッド内組み立て",
                    "endpoint": "OpenAI(Azure)/OpenRouter",
                },
                "review": {
                    "label": "レビュー",
                    "role": "品質/論理性レビュー",
                    "prompt_source": "メソッド内組み立て",
                    "endpoint": "OpenAI(Azure)/OpenRouter",
                },
                "enhance": {
                    "label": "エンハンス",
                    "role": "文章強化・拡張",
                    "prompt_source": "メソッド内組み立て",
                    "endpoint": "OpenAI(Azure)/OpenRouter",
                },
                "script_polish_ai": {
                    "label": "台本ポリッシュ",
                    "role": "Stage8 ポリッシュ",
                    "prompt_source": "packages/script_pipeline/prompts/llm_polish_template.txt + workspaces/planning/personas/{CH}_PERSONA.md",
                    "endpoint": "OpenAI(Azure)優先 / OpenRouter fallback",
                },
                "audio_text": {
                    "label": "音声テキスト生成(改行最適化)",
                    "role": "27/54文字制約の改行最適化",
                    "prompt_source": "メソッド内組み立て",
                    "endpoint": "Gemini or OpenAI(Azure) if forced",
                },
                "image_generation": {
                    "label": "画像生成",
                    "role": "Gemini画像生成",
                    "path": "packages/video_pipeline/src/srt2images/nanobanana_client.py::_run_direct",
                    "prompt_source": "呼び出し元渡し（固定プロンプトなし）",
                    "endpoint": "Gemini 2.5 Flash Image Preview",
                },
                "context_analysis": {
                    "label": "文脈解析",
                    "role": "SRTセクション分割",
                    "path": "packages/video_pipeline/src/srt2images/llm_context_analyzer.py::LLMContextAnalyzer.analyze_story_sections",
                    "prompt_source": "_create_analysis_prompt（動的生成）",
                    "endpoint": "Gemini 2.5 Pro",
                },
            }
        )
        # model/providerを phase_models から補完
        pm = llm.get("phase_models") or {}
        for pid, info in pm.items():
            det = details.setdefault(pid, {})
            det["provider"] = info.get("provider")
            det["model"] = info.get("model")
            det.setdefault("label", info.get("label") or pid)
        return details

    config = LLMConfig(
        caption_provider=llm.get("caption_provider") or "openai",
        openai_api_key=llm.get("openai_api_key"),
        openai_caption_model=llm.get("openai_caption_model"),
        openrouter_api_key=llm.get("openrouter_api_key"),
        openrouter_caption_model=llm.get("openrouter_caption_model"),
        openai_key_configured=bool(llm.get("openai_api_key") or openai_env_key),
        openrouter_key_configured=bool(llm.get("openrouter_api_key") or openrouter_env_key),
        openai_models=openai_models,
        openrouter_models=openrouter_models,
        openai_key_preview=_mask_secret(llm.get("openai_api_key") or openai_env_key),
        openrouter_key_preview=_mask_secret(llm.get("openrouter_api_key") or openrouter_env_key),
        openai_models_error=openai_models_error,
        openrouter_models_error=openrouter_models_error,
        phase_models=_export_phase_models(),
        phase_details=_export_phase_details(),
    )
    return LLMSettingsResponse(llm=config)

