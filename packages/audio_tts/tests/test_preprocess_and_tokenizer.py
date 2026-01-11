import pytest

from audio_tts.tts.arbiter import _patch_tokens_with_words
from audio_tts.tts.mecab_tokenizer import tokenize_with_mecab
from audio_tts.tts.preprocess import preprocess_a_text


def test_preprocess_and_silence_detection():
    text = "\ufeff 今日はテストです。[0.5]\n"
    res = preprocess_a_text(text)
    assert res["a_text"].startswith("今日は")
    assert res["meta"]["silence_tags"]


def test_tokenizer_inserts_silence(monkeypatch):
    try:
        import MeCab  # noqa: F401
    except ImportError:
        pytest.skip("MeCab not installed")
    tokens = tokenize_with_mecab("重力波観測[0.5]成功")
    assert any(t.get("pos") == "silence_tag" for t in tokens)


def test_patch_tokens_strips_inline_reading_hints():
    tokens = tokenize_with_mecab("新潟の刈羽郡、かりわぐんから、たった一人で東京へ出てきた。")
    out = _patch_tokens_with_words(tokens, words={}, override_map={})
    assert out == "新潟の刈羽郡から、たった一人で東京へ出てきた。"


def test_patch_tokens_strips_inline_ascii_hint_when_dict_matches():
    tokens = tokenize_with_mecab("そして禅、Zenに出会った。")
    out = _patch_tokens_with_words(tokens, words={"Zen": "ゼン"}, override_map={})
    assert out == "そして禅に出会った。"
