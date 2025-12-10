import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))


@pytest.fixture(scope="module")
def mecab_available():
    try:
        import MeCab  # noqa: F401
    except ImportError:
        pytest.skip("MeCab not available")
    return True


def test_mecab_tokenizer_inserts_silence_tag(mecab_available):  # noqa: ARG001
    from audio.tts.mecab_tokenizer import tokenize_with_mecab

    text = "重力波観測[0.5]成功しました"
    tokens = tokenize_with_mecab(text)

    has_silence = any(t.get("pos") == "silence_tag" and t.get("surface") == "[0.5]" for t in tokens)
    assert has_silence
    assert tokens[0]["surface"] == "重力波"
