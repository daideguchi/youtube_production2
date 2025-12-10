from audio_tts_v2.tts.reading_structs import (
    RubyLine,
    RubyToken,
    align_moras_with_tokens,
    evaluate_reading_diffs,
)


def test_align_moras_with_tokens_basic():
    accent_phrases = [
        {"moras": [{"text": "カ"}, {"text": "ン"}, {"text": "ジ"}]},
    ]
    tokens = [
        RubyToken(surface="漢", reading_hira="かん", token_index=0, line_id=0),
        RubyToken(surface="字", reading_hira="じ", token_index=1, line_id=0),
    ]

    aligned = align_moras_with_tokens(accent_phrases, tokens)

    assert len(aligned) == 2
    assert aligned[0][1] == ["カ", "ン"]
    assert aligned[1][1] == ["ジ"]


def test_evaluate_reading_diffs_flags_mismatch():
    line = RubyLine(
        line_id=0,
        text="辛い",
        tokens=[
            RubyToken(surface="辛い", reading_hira="からい", token_index=0, line_id=0),
        ],
    )
    aligned = align_moras_with_tokens(
        accent_phrases=[{"moras": [{"text": "ツ"}, {"text": "ラ"}, {"text": "イ"}]}],
        tokens=line.tokens,
    )

    risky = evaluate_reading_diffs(aligned)

    assert len(risky) == 1
    span = risky[0]
    assert span.line_id == 0
    assert "ツライ" in span.reason


