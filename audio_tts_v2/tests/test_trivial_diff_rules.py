import sys
from pathlib import Path

# Ensure repository root on path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audio_tts_v2.tts.risk_utils import is_trivial_diff


def test_trivial_diff_allows_single_char_variation():
    # 「キョウ」vs「キョオ」は 1 文字揺れとしてスキップ扱い
    assert is_trivial_diff("キョウ", "キョオ") is True


def test_trivial_diff_flags_semantic_changes():
    # 「ツライ」vs「カライ」は意味が変わるので監査対象
    assert is_trivial_diff("ツライ", "カライ") is False
    # 「オコリ」vs「イカリ」も監査対象
    assert is_trivial_diff("オコリ", "イカリ") is False

