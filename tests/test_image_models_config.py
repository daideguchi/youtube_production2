import yaml
from pathlib import Path


def test_image_models_use_image_gen_tier():
    cfg_path = Path("configs/image_models.yaml")
    data = yaml.safe_load(cfg_path.read_text())

    tiers = data.get("tiers", {})
    tasks = data.get("tasks", {})

    # tier name (legacy/default)
    assert "image_gen" in tiers, "image_gen tier must exist"
    image_gen = tiers["image_gen"]
    assert isinstance(image_gen, list) and image_gen, "image_gen tier must be a non-empty list"
    assert image_gen[0] == "gemini_2_5_flash_image"
    assert "openrouter_gemini_2_5_flash_image_preview" in image_gen
    assert "openrouter_gemini_2_5_flash_image" in image_gen

    # video image gen tier (CapCut bulk)
    assert "video_image_gen" in tiers, "video_image_gen tier must exist"
    video_tier = tiers["video_image_gen"]
    assert isinstance(video_tier, list) and video_tier, "video_image_gen tier must be a non-empty list"
    assert video_tier[0] == "fireworks_flux_1_schnell_fp8"

    thumb_tier = tiers.get("thumbnail_image_gen")
    assert isinstance(thumb_tier, list) and thumb_tier, "thumbnail_image_gen tier must be a non-empty list"
    assert thumb_tier == ["fireworks_flux_kontext_max"]

    # tasks point to video_image_gen (bulk video images)
    for task in ("image_generation", "visual_image_gen"):
        assert task in tasks, f"{task} must be defined"
        assert tasks[task].get("tier") == "video_image_gen", f"{task} should use video_image_gen tier"
        defaults = tasks[task].get("defaults", {})
        assert defaults.get("aspect_ratio") == "16:9"
        assert defaults.get("size") == "1920x1080"
        assert defaults.get("n") == 1
