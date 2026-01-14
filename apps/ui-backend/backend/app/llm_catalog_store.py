from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import HTTPException

from backend.app.ui_settings_store import _load_env_value
from script_pipeline.tools import openrouter_models as openrouter_model_utils

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_GENERATION_URL = "https://openrouter.ai/api/v1/generation"

OPENROUTER_MODELS_CACHE_LOCK = threading.Lock()
OPENROUTER_MODELS_CACHE: Dict[str, Any] = {"fetched_at": 0.0, "pricing_by_id": {}}
OPENROUTER_MODELS_CACHE_TTL_SEC = 60 * 60


def _fetch_openrouter_model_ids_via_rest(api_key: str) -> List[str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    referer = os.getenv("OPENROUTER_REFERRER")
    title = os.getenv("OPENROUTER_TITLE")
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    try:
        response = requests.get(OPENROUTER_MODELS_URL, headers=headers, timeout=30)
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise HTTPException(status_code=502, detail=f"OpenRouter モデル一覧の取得に失敗しました: {exc}") from exc
    if response.status_code == 401:
        raise HTTPException(status_code=400, detail="OpenRouter APIキーが無効です。")
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"OpenRouter モデル一覧取得エラー: {response.text}")
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="OpenRouter モデル一覧が不正な形式でした。") from exc
    models: List[str] = []
    for entry in data.get("data") or []:
        model_id = entry.get("id")
        if isinstance(model_id, str):
            models.append(model_id)
    return models


def _list_openai_model_ids(api_key: str) -> List[str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.get("https://api.openai.com/v1/models", headers=headers, timeout=30)
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise HTTPException(status_code=502, detail=f"OpenAI モデル一覧の取得に失敗しました: {exc}") from exc
    if response.status_code == 401:
        raise HTTPException(status_code=400, detail="OpenAI APIキーが無効です。")
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"OpenAI モデル一覧取得エラー: {response.text}")
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="OpenAI モデル一覧が不正な形式でした。") from exc
    models: List[str] = []
    for entry in data.get("data") or []:
        model_id = entry.get("id")
        if isinstance(model_id, str):
            models.append(model_id)
    return models


def _list_openrouter_model_ids(api_key: str) -> List[str]:
    """Return OpenRouter model IDs prioritizing recommended free tiers, but still exposing the full catalog."""
    curated_models: List[str] = []
    previous_key = os.environ.get("OPENROUTER_API_KEY")
    try:
        os.environ["OPENROUTER_API_KEY"] = api_key
        curated_models = openrouter_model_utils.get_free_model_candidates(refresh=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load OpenRouter free model list via 01_secretary logic: %s", exc)
    finally:
        if previous_key is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = previous_key
    all_models: List[str] = []
    try:
        all_models = _fetch_openrouter_model_ids_via_rest(api_key)
    except HTTPException as exc:
        if not curated_models:
            raise
        logger.warning("OpenRouter REST model list failed, falling back to curated list only: %s", exc.detail)
    merged: List[str] = []
    for model_id in curated_models + all_models:
        if isinstance(model_id, str) and model_id not in merged:
            merged.append(model_id)
    return merged or curated_models or all_models


def _get_openrouter_pricing_by_model_id(
    *, max_age_sec: int = OPENROUTER_MODELS_CACHE_TTL_SEC, timeout_sec: int = 10
) -> Tuple[Dict[str, Dict[str, str]], float]:
    """
    Fetch OpenRouter model pricing table (best-effort) from `/api/v1/models`.

    Returns:
      - pricing_by_id: { model_id: { pricing_key: unit_price_str } }
      - fetched_at_epoch: seconds since epoch (UTC)
    """
    now = time.time()
    with OPENROUTER_MODELS_CACHE_LOCK:
        fetched_at = float(OPENROUTER_MODELS_CACHE.get("fetched_at") or 0.0)
        cached = OPENROUTER_MODELS_CACHE.get("pricing_by_id")
        if isinstance(cached, dict) and cached and (now - fetched_at) < max_age_sec:
            return cached, fetched_at

    try:
        resp = requests.get(OPENROUTER_MODELS_URL, timeout=timeout_sec)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        with OPENROUTER_MODELS_CACHE_LOCK:
            fetched_at = float(OPENROUTER_MODELS_CACHE.get("fetched_at") or 0.0)
            cached = OPENROUTER_MODELS_CACHE.get("pricing_by_id")
            if isinstance(cached, dict) and cached:
                return cached, fetched_at
        return {}, 0.0

    models = payload.get("data") if isinstance(payload, dict) else None
    pricing_by_id: Dict[str, Dict[str, str]] = {}
    if isinstance(models, list):
        for model in models:
            if not isinstance(model, dict):
                continue
            model_id = str(model.get("id") or "").strip()
            if not model_id:
                continue
            pricing = model.get("pricing")
            if not isinstance(pricing, dict):
                continue
            normalized: Dict[str, str] = {}
            for key, val in pricing.items():
                if val is None:
                    continue
                if isinstance(val, (int, float)):
                    normalized[str(key)] = str(val)
                elif isinstance(val, str):
                    normalized[str(key)] = val
            if normalized:
                pricing_by_id[model_id] = normalized

    with OPENROUTER_MODELS_CACHE_LOCK:
        OPENROUTER_MODELS_CACHE["fetched_at"] = now
        OPENROUTER_MODELS_CACHE["pricing_by_id"] = pricing_by_id

    return pricing_by_id, now


def _fetch_openrouter_generation(gen_id: str, *, timeout_sec: int = 10) -> Optional[Dict[str, Any]]:
    """
    Fetch OpenRouter generation metadata (includes billed cost) from `/api/v1/generation`.

    Docs: https://openrouter.ai/docs/api-reference/get-a-generation
    """
    if not gen_id:
        return None
    key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_TOKEN") or _load_env_value("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        resp = requests.get(
            OPENROUTER_GENERATION_URL,
            headers={"Authorization": f"Bearer {key}"},
            params={"id": gen_id},
            timeout=timeout_sec,
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            return payload["data"]
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None

