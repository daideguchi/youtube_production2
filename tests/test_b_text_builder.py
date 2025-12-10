import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from audio.tts.builder import build_b_text  # noqa: E402


def test_build_b_text_hiragana_and_original_gap():
    a_text = "今日は東京で行われた防災訓練について解説します。"
    tokens = [
        {"index": 0, "surface": "今日", "char_start": 0, "char_end": 2},
        {"index": 1, "surface": "は", "char_start": 2, "char_end": 3},
        {"index": 2, "surface": "東京", "char_start": 3, "char_end": 5},
    ]
    annotations = [
        {"index": 0, "write_mode": "hiragana", "llm_reading_kana": "キョウ"},
        {"index": 2, "write_mode": "original"},
    ]

    b_text, log = build_b_text(a_text, tokens, annotations)

    assert b_text.startswith("きょうは東京")
    assert len(log) == 3
    assert log[0]["replaced_fragment"] == "きょう"
    assert log[1]["replaced_fragment"] == "は"
    assert log[2]["replaced_fragment"] == "東京"


def test_build_b_text_katakana_and_silence_tag():
    a_text = "重力波観測[0.5]成功しました"
    tokens = [
        {"index": 0, "surface": "重力波", "char_start": 0, "char_end": 3},
        {"index": 1, "surface": "観測", "char_start": 3, "char_end": 5},
        {"index": 2, "surface": "[0.5]", "char_start": 5, "char_end": 10, "pos": "silence_tag"},
        {"index": 3, "surface": "成功", "char_start": 10, "char_end": 12},
        {"index": 4, "surface": "しました", "char_start": 12, "char_end": 15},
    ]
    annotations = [
        {"index": 1, "write_mode": "katakana", "llm_reading_kana": "カンソク"},
        {"index": 3, "write_mode": "hiragana", "llm_reading_kana": "セイコウ"},
    ]

    b_text, log = build_b_text(a_text, tokens, annotations)

    assert "カンソク" in b_text
    assert "せいこう" in b_text
    assert "[0.5]" in b_text
    assert log[2]["original_fragment"] == "[0.5]"
