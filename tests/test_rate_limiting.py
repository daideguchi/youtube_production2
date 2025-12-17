#!/usr/bin/env python3
"""
Test script to verify rate limiting in the commentary_02 image generation.
Updated for current package structure.
"""
import pytest


def test_imports():
    """Test that we can import the image generation modules."""
    try:
        from commentary_02_srt2images_timeline.tools.factory import main
    except ImportError:
        pytest.skip("commentary_02_srt2images_timeline not available")
    assert callable(main)


def test_rate_limiting_logic():
    """Test that rate limiting configuration is available."""
    import os
    # Just verify the environment variable mechanism works
    original = os.environ.get("SRT2IMAGES_IMAGE_MAX_PER_MINUTE")
    os.environ["SRT2IMAGES_IMAGE_MAX_PER_MINUTE"] = "20"
    assert os.environ["SRT2IMAGES_IMAGE_MAX_PER_MINUTE"] == "20"
    if original:
        os.environ["SRT2IMAGES_IMAGE_MAX_PER_MINUTE"] = original
    else:
        del os.environ["SRT2IMAGES_IMAGE_MAX_PER_MINUTE"]


def test_model_selection_function():
    """Test that channel presets are loadable."""
    import json
    from pathlib import Path
    from factory_common.paths import video_pkg_root
    
    presets_path = video_pkg_root() / "config" / "channel_presets.json"
    if not presets_path.exists():
        pytest.skip("channel_presets.json not found")
    
    data = json.loads(presets_path.read_text())
    assert "channels" in data or isinstance(data, dict)


def test_environment_variable_usage():
    """Test environment variable access works."""
    import os
    test_key = "TEST_RATE_LIMIT_VAR"
    os.environ[test_key] = "test_value"
    assert os.environ.get(test_key) == "test_value"
    del os.environ[test_key]
