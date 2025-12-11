from pathlib import Path
from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException, Query
import json
import yaml

LOG_PATH = Path("logs/llm_usage.jsonl")
OVERRIDE_PATH = Path("configs/llm_task_overrides.yaml")
MODEL_REGISTRY_PATH = Path("configs/llm_model_registry.yaml")
IMAGE_MODELS_PATH = Path("configs/image_models.yaml")

router = APIRouter(prefix="/llm-usage", tags=["llm_usage"])


def _load_records(limit: int) -> List[Dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    records = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records[-limit:] if limit else records


@router.get("/")
def list_usage(limit: int = Query(200, ge=1, le=2000)):
    """
    Return recent LLM router usage logs (JSONL entries).
    """
    records = _load_records(limit)
    return {"count": len(records), "records": records}


@router.get("/overrides")
def get_overrides():
    """
    Return current task overrides (YAML content as dict). Empty dict if missing.
    """
    if not OVERRIDE_PATH.exists():
        return {"tasks": {}}
    try:
        data = yaml.safe_load(OVERRIDE_PATH.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError("override file is not a mapping")
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read overrides: {e}")


@router.post("/overrides")
def set_overrides(body: Dict[str, Any]):
    """
    Replace task overrides (expects dict with top-level 'tasks').
    Validates model keys against llm_model_registry + image_models.
    """
    if not isinstance(body, dict) or "tasks" not in body:
        raise HTTPException(status_code=400, detail="Payload must include 'tasks' mapping")
    tasks = body.get("tasks") or {}
    if not isinstance(tasks, dict):
        raise HTTPException(status_code=400, detail="'tasks' must be a mapping")

    allowed = _allowed_models()
    for task, conf in tasks.items():
        if not isinstance(conf, dict):
            raise HTTPException(status_code=400, detail=f"Invalid override format for task {task}")
        models = conf.get("models") or []
        if not isinstance(models, list):
            raise HTTPException(status_code=400, detail=f"'models' must be a list for task {task}")
        for m in models:
            if allowed and m not in allowed:
                raise HTTPException(status_code=400, detail=f"Unknown model key {m} for task {task}")

    try:
        OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
        OVERRIDE_PATH.write_text(yaml.safe_dump({"tasks": tasks}, allow_unicode=True, sort_keys=False))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write overrides: {e}")
    return {"status": "ok", "tasks": tasks}


@router.get("/models")
def list_models():
    """
    Return available model keys (llm_model_registry + image_models).
    """
    models = set()
    if MODEL_REGISTRY_PATH.exists():
        try:
            data = yaml.safe_load(MODEL_REGISTRY_PATH.read_text()) or {}
            if isinstance(data, dict):
                models.update(data.get("models", {}).keys())
        except Exception:
            pass
    if IMAGE_MODELS_PATH.exists():
        try:
            data = yaml.safe_load(IMAGE_MODELS_PATH.read_text()) or {}
            if isinstance(data, dict):
                models.update(data.get("models", {}).keys())
        except Exception:
            pass
    return {"models": sorted(models)}


def _allowed_models() -> set:
    return set(list_models()["models"])
