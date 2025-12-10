import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from audio.tts.preprocess import preprocess_a_text  # noqa: E402


def test_preprocess_removes_bom_and_trims_and_detects_silence():
    text = "\ufeff 今日はテストです。[0.5] よろしくお願いします。\n"
    result = preprocess_a_text(text)
    cleaned = result["a_text"]
    meta = result["meta"]

    assert cleaned.startswith("今日は")
    assert cleaned.endswith("。")
    assert meta["silence_tags"][0]["tag"] == "[0.5]"


def test_preprocess_control_char_warning():
    text = "制御\x01文字"
    result = preprocess_a_text(text)
    warnings = result["meta"]["warnings"]
    assert warnings and warnings[0]["type"] == "control_char"
