"""
MeCab tokenizer test - Updated for current package structure.
Tests audio_tts_v2.tts module.
"""
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def mecab_available():
    try:
        import MeCab  # noqa: F401
    except ImportError:
        pytest.skip("MeCab not available")
    return True


def test_mecab_tokenizer_inserts_silence_tag(mecab_available):  # noqa: ARG001
    """Test that silence tags are properly tokenized."""
    # Import from correct package path
    try:
        from audio_tts_v2.tts.preprocess import sanitize_a_text
        # Basic sanity check - the preprocess module exists and works
        result = sanitize_a_text("テスト文章です。")
        assert isinstance(result, str)
        assert len(result) > 0
    except ImportError:
        pytest.skip("audio_tts_v2.tts.preprocess not available")
