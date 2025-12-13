"""OpenRouter model metadata helper.

- `fetch_models` CLI は引き続き利用可（openrouter_models.json への書き出し）
- UI/backend からは get_free_model_candidates() を使用
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from factory_common.paths import repo_root, script_pkg_root

PROJECT_ROOT = repo_root()
CONFIG_PATH = PROJECT_ROOT / "configs" / "openrouter_models.json"
OUTPUT_PATH = script_pkg_root() / "config" / "openrouter_models.json"
MODELS_URL = "https://openrouter.ai/api/v1/models"


def is_free_model(model: Dict[str, Any]) -> bool:
    """Decide if a model is free based on id suffix or pricing prompt/completion == 0."""
    mid = model.get("id", "")
    if ":free" in mid:
        return True
    pricing = model.get("pricing") or {}
    try:
        p_prompt = float(pricing.get("prompt", "0") or "0")
        p_comp = float(pricing.get("completion", "0") or "0")
    except Exception:
        return False
    return p_prompt == 0.0 and p_comp == 0.0


def compute_max_completion(model: Dict[str, Any]) -> int:
    """Derive a safe default max_completion_tokens for a model.

    優先順位:
      1) top_provider.max_completion_tokens
      2) per_request_limits.completion_tokens
      3) context_length // 2 (プロンプト分の余白)
      4) fallback 4096
    """
    top = model.get("top_provider") or {}
    pr_limits = model.get("per_request_limits") or {}

    for source in (top.get("max_completion_tokens"), pr_limits.get("completion_tokens")):
        if source is not None:
            try:
                val = int(source)
                if val > 0:
                    return val
            except Exception:
                continue

    ctx = model.get("context_length")
    try:
        ctx_int = int(ctx)
        return max(1024, ctx_int // 2)
    except Exception:
        return 4096


def fetch_models(free_only: bool = False) -> List[Dict[str, Any]]:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    resp = requests.get(MODELS_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    out: List[Dict[str, Any]] = []
    for m in data:
        if free_only and not is_free_model(m):
            continue
        supported_parameters = m.get("supported_parameters") or []
        default_parameters = m.get("default_parameters") or {}
        entry = {
            "id": m.get("id"),
            "name": m.get("name"),
            "context_length": m.get("context_length"),
            "per_request_limits": m.get("per_request_limits"),
            "pricing": m.get("pricing"),
            "top_provider": m.get("top_provider"),
            "supported_parameters": supported_parameters,
            "default_parameters": default_parameters,
            "is_free": is_free_model(m),
            "default_max_completion_tokens": compute_max_completion(m),
        }
        out.append(entry)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--free-only", action="store_true", help="Keep only :free / price=0 models")
    args = ap.parse_args()

    models = fetch_models(free_only=args.free_only)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(models, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(models)} models to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

# Helpers for UI/backend -----------------------------------------------------

def _load_cached_models() -> List[Dict[str, Any]]:
    for p in (CONFIG_PATH, OUTPUT_PATH):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return []


def get_free_model_candidates(refresh: bool = False) -> List[Dict[str, Any]]:
    """Return free models metadata (id/name/is_free/default_max_completion_tokens)."""
    models: List[Dict[str, Any]] = []
    if not refresh:
        models = _load_cached_models()
    if refresh or not models:
        try:
            models = fetch_models(free_only=False)
        except Exception:
            models = _load_cached_models()
    out: List[Dict[str, Any]] = []
    for m in models:
        if not is_free_model(m):
            continue
        out.append(
            {
                "id": m.get("id"),
                "name": m.get("name"),
                "is_free": True,
                "default_max_completion_tokens": m.get("default_max_completion_tokens"),
                "supported_parameters": m.get("supported_parameters"),
                "default_parameters": m.get("default_parameters"),
            }
        )
    return out


def get_model_meta(model_id: str) -> Optional[Dict[str, Any]]:
    models = {m.get("id"): m for m in _load_cached_models() if m.get("id")}
    return models.get(model_id)


def get_default_parameters(model_id: str) -> Dict[str, Any]:
    meta = get_model_meta(model_id) or {}
    return meta.get("default_parameters") or {}


def get_supported_parameters(model_id: str) -> List[str]:
    meta = get_model_meta(model_id) or {}
    return meta.get("supported_parameters") or []
