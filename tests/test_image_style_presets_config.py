from __future__ import annotations

from factory_common.paths import repo_root


def test_image_style_presets_yaml_loads():
    path = repo_root() / "configs" / "image_style_presets.yaml"
    assert path.exists()

    import yaml  # local import to keep dependency optional in other contexts

    conf = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(conf, dict)
    presets = conf.get("presets")
    assert isinstance(presets, dict)
    assert len(presets) >= 5

    for key, value in presets.items():
        assert isinstance(key, str) and key.strip()
        if isinstance(value, str):
            assert value.strip()
            continue
        assert isinstance(value, dict)
        prompt = value.get("prompt")
        assert isinstance(prompt, str) and prompt.strip()

