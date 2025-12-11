from factory_common.llm_config import load_llm_config, resolve_task


def test_visual_tasks_use_expected_tiers_and_models():
    cfg = load_llm_config()

    # text-side visual tasks
    text_tasks = ["visual_section_plan", "visual_prompt_from_chunk", "visual_persona"]
    for t in text_tasks:
        info = resolve_task(cfg, t)
        assert info["tier"] != "image_gen"
        assert info["models"] == ["azure_gpt5_mini"]

    # image-side tasks
    img_tasks = ["visual_image_gen", "image_generation"]
    for t in img_tasks:
        info = resolve_task(cfg, t)
        assert info["tier"] == "image_gen"
        assert info["models"] == ["gemini_2_5_flash_image"]
