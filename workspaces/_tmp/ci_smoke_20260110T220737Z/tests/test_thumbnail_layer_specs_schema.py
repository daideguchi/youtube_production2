#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import yaml
import pytest

from factory_common import paths as fpaths
from script_pipeline.thumbnails.compiler.layer_specs_schema_v3 import (
    LayerSpecValidationError,
    parse_image_prompts_v3,
    parse_text_layout_v3,
)


def test_layer_specs_v3_yaml_parses() -> None:
    root = fpaths.thumbnails_root() / "compiler" / "layer_specs"
    img_path = root / "image_prompts_v3.yaml"
    txt_path = root / "text_layout_v3.yaml"

    img_doc = yaml.safe_load(img_path.read_text(encoding="utf-8"))
    txt_doc = yaml.safe_load(txt_path.read_text(encoding="utf-8"))

    img = parse_image_prompts_v3(img_doc, spec_path=str(img_path))
    txt = parse_text_layout_v3(txt_doc, spec_path=str(txt_path))

    assert img.version == 3
    assert txt.version == 3
    assert img.items
    assert txt.items
    assert txt.templates


def test_image_prompts_v3_missing_prompt_raises() -> None:
    bad = {
        "version": 3,
        "name": "X",
        "canvas": {"w": 1920, "h": 1080, "aspect": "16:9"},
        "policy": {},
        "items": [{"video_id": "CH10-001", "title": "t", "person_key": "p", "prompt_ja": ""}],
    }
    with pytest.raises(LayerSpecValidationError):
        parse_image_prompts_v3(bad, spec_path="test://image_prompts_v3")


def test_text_layout_v3_unknown_fill_key_raises() -> None:
    base = {
        "version": 3,
        "name": "X",
        "canvas": {"w": 1920, "h": 1080, "aspect": "16:9"},
        "coordinate_system": "normalized_0_to_1",
        "global": {
            "fonts": {"headline_sans_priority": ["Noto Sans JP Black"]},
            "effects_defaults": {
                "stroke": {"color": "#000000", "width_px": 10, "join": "round"},
                "shadow": {"color": "#000000", "alpha": 0.9, "offset_px": [12, 12], "blur_px": 10},
                "glow": {"color": "#ffffff", "alpha": 0.1, "blur_px": 5},
                "white_fill": {"mode": "solid", "color": "#ffffff"},
            },
        },
        "templates": {
            "T1": {
                "slots": {
                    "main": {
                        "box": [0.0, 0.0, 1.0, 1.0],
                        "font": "headline_sans_priority",
                        "fill": "MISSING_FILL",
                        "base_size_px": 100,
                        "align": "left",
                        "max_lines": 2,
                    }
                }
            }
        },
        "items": [{"video_id": "CH10-001", "title": "t", "template_id": "T1", "text": {"main": "hello"}}],
    }
    with pytest.raises(LayerSpecValidationError):
        parse_text_layout_v3(base, spec_path="test://text_layout_v3")

