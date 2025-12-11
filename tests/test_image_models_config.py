import yaml
from pathlib import Path


def test_image_models_use_image_gen_tier():
    cfg_path = Path("configs/image_models.yaml")
    data = yaml.safe_load(cfg_path.read_text())

    tiers = data.get("tiers", {})
    tasks = data.get("tasks", {})

    # tier name
    assert "image_gen" in tiers, "image_gen tier must exist"
    assert tiers["image_gen"] == ["gemini_2_5_flash_image"]

    # tasks point to image_gen
    for task in ("image_generation", "visual_image_gen"):
        assert task in tasks, f"{task} must be defined"
        assert tasks[task].get("tier") == "image_gen", f"{task} should use image_gen tier"
        defaults = tasks[task].get("defaults", {})
        assert defaults.get("aspect_ratio") == "16:9"
        assert defaults.get("n") == 1
