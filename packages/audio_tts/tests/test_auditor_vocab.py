import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

from audio_tts.tts.auditor import _build_vocab_requests
from audio_tts.tts.reading_structs import RiskySpan, RubyToken


def test_build_vocab_requests_groups_and_limits_examples():
    tokens_by_block = {
        0: [RubyToken(surface="怒り", reading_hira="いかり", token_index=0, line_id=0)],
        1: [RubyToken(surface="怒り", reading_hira="いかり", token_index=0, line_id=1)],
    }
    blocks_by_index = {
        0: {"text": "怒りを鎮める", "voicevox_kana": "イカリ"},
        1: {"text": "怒りが爆発", "voicevox_kana": "イカリ"},
    }
    spans = [
        RiskySpan(line_id=0, token_index=0, risk_score=1.0, reason="hazard:怒り", surface="怒り"),
        RiskySpan(line_id=1, token_index=0, risk_score=1.0, reason="block_diff", surface="怒り"),
    ]

    requests = _build_vocab_requests(
        spans,
        tokens_by_block=tokens_by_block,
        blocks_by_index=blocks_by_index,
        max_examples=1,
    )

    assert len(requests) == 1
    req = requests[0]
    assert req["surface"] == "怒り"
    # vocab LLM は hazard レベルのみ対象
    assert req["reasons"] == ["hazard:怒り"]
    assert req["examples"] == ["怒りを鎮める"]  # capped to max_examples=1
    assert req["mecab_kana"] == "いかり"
    assert req["voicevox_kana"] == "イカリ"
