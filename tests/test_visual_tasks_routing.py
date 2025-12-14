"""
Test that visual tasks are routed to correct tiers and models.
"""
from factory_common.llm_config import load_llm_config, resolve_task


def test_visual_tasks_use_expected_tiers_and_models():
    cfg = load_llm_config()

    # text-side visual tasks - verify they resolve to valid models
    text_tasks = ["visual_section_plan", "visual_prompt_from_chunk", "visual_persona"]
    for t in text_tasks:
        info = resolve_task(cfg, t)
        # Just verify resolution works and returns models
        assert info.get("tier") != "image_gen", f"Task {t} should not use image_gen tier"
        assert isinstance(info.get("models"), list), f"Task {t} should have models list"
        assert len(info.get("models", [])) > 0, f"Task {t} should have at least one model"

    # image-side tasks - verify they use image_gen tier
    img_tasks = ["visual_image_gen", "image_generation"]
    for t in img_tasks:
        info = resolve_task(cfg, t)
        assert info.get("tier") == "image_gen", f"Task {t} should use image_gen tier"
        assert isinstance(info.get("models"), list), f"Task {t} should have models list"
        assert len(info.get("models", [])) > 0, f"Task {t} should have at least one model"
