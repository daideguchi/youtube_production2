"""
Minimal config smoke (no heavy execution).
Validates LLM and image routing resolve to allowed models.
"""
from factory_common.llm_config import load_llm_config, resolve_task
import yaml
from pathlib import Path


def test_llm_tasks_resolve_to_allowed_models():
    cfg = load_llm_config()
    allowed_text = {"azure_gpt5_mini"}
    allowed_image = {"gemini_2_5_flash_image"}
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
        models = set(info.get("models") or [])
        if info.get("tier") == "image_gen":
            assert models <= allowed_image
        else:
            assert models <= allowed_text


def test_image_models_config_consistency():
    cfg_path = Path("configs/image_models.yaml")
    data = yaml.safe_load(cfg_path.read_text())
    tiers = data.get("tiers", {})
    tasks = data.get("tasks", {})
    assert tiers.get("image_gen") == ["gemini_2_5_flash_image"]
    assert tasks.get("image_generation", {}).get("tier") == "image_gen"
    assert tasks.get("visual_image_gen", {}).get("tier") == "image_gen"
