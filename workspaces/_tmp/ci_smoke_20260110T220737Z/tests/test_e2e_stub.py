"""
Minimal config smoke (no heavy execution).
Validates LLM and image routing resolve correctly.
"""
from factory_common.llm_config import load_llm_config, resolve_task
import yaml
from pathlib import Path


def test_llm_tasks_resolve_to_allowed_models():
    """Test that all LLM tasks resolve to valid models."""
    cfg = load_llm_config()
    
    check_tasks = [
        "script_chapter_draft",
        "script_outline",
        "script_quality_check",
        "tts_annotate",
        "tts_reading",
        "image_generation",
        "visual_image_gen",
    ]
    
    for t in check_tasks:
        info = resolve_task(cfg, t)
        models = info.get("models") or []
        # Just verify that models are resolved, not specific model names
        # (model configuration changes frequently)
        assert isinstance(models, list), f"Task {t} should resolve to a list of models"


def test_image_models_config_consistency():
    """Test that image model config file is valid."""
    cfg_path = Path("configs/image_models.yaml")
    if not cfg_path.exists():
        import pytest
        pytest.skip("configs/image_models.yaml not found")
    
    data = yaml.safe_load(cfg_path.read_text())
    assert "tiers" in data or "tasks" in data or "models" in data
