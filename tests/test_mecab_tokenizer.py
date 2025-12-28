"""
MeCab tokenizer test - Updated for current package structure.
Tests `audio_tts.tts.mecab_tokenizer`.
"""

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
    from audio_tts.tts.mecab_tokenizer import tokenize_with_mecab

    text = "こんにちは[1.2]さようなら"
    tokens = tokenize_with_mecab(text)

    silence = [t for t in tokens if t.get("pos") == "silence_tag"]
    assert len(silence) == 1
    assert silence[0]["surface"] == "[1.2]"
