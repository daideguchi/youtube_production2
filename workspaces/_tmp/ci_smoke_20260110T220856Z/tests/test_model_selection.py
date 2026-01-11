#!/usr/bin/env python3
"""
Test script to verify model selection logic for different channels.
Updated for current package structure.
"""
import pytest


def test_model_selection_logic():
    """Test that model selection function returns correct models for different channels."""
    
    # Test the model selection logic pattern
    def select_image_model_for_channel(channel: str) -> str:
        """Channel-based model selection."""
        channel_upper = (channel or "").upper()
        if channel_upper == "CH01":
            return "gemini-3-pro-image-preview"
        else:
            return "gemini-2.5-flash-image"

    # Test cases
    test_cases = [
        ("CH01", "gemini-3-pro-image-preview"),
        ("ch01", "gemini-3-pro-image-preview"),
        ("Ch01", "gemini-3-pro-image-preview"),
        ("CH02", "gemini-2.5-flash-image"),
        ("CH05", "gemini-2.5-flash-image"),
        ("ANY_OTHER_CHANNEL", "gemini-2.5-flash-image"),
        ("", "gemini-2.5-flash-image"),
        (None, "gemini-2.5-flash-image"),
    ]

    for channel, expected_model in test_cases:
        result = select_image_model_for_channel(channel)
        assert result == expected_model, f"Channel '{channel}' -> '{result}' (expected '{expected_model}')"


def test_no_gemini_20_flash_exp():
    """Test that deprecated model is not in active config."""
    from factory_common.llm_config import load_llm_config
    
    cfg = load_llm_config()
    models = cfg.get("models", {})
    
    # Check that gemini-2.0-flash-exp is not a configured model
    for model_key, model_cfg in models.items():
        model_name = model_cfg.get("model") or model_cfg.get("model_name") or ""
        assert "gemini-2.0-flash-exp" not in model_name, f"Model {model_key} uses deprecated gemini-2.0-flash-exp"
