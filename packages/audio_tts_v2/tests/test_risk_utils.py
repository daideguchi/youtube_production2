from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from audio_tts_v2.tts.reading_dict import (
    ReadingEntry,
    export_words_for_word_dict,
    load_channel_reading_dict,
    merge_channel_readings,
)
from audio_tts_v2.tts.reading_structs import RiskySpan, RubyToken
from audio_tts_v2.tts import risk_utils


def test_reading_dict_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(risk_utils, "READING_DICT_ROOT", tmp_path, raising=False)
    from audio_tts_v2.tts import reading_dict

    monkeypatch.setattr(reading_dict, "READING_DICT_ROOT", tmp_path)
    channel = "CH_TEST"
    entry = ReadingEntry(
        surface="白隠禅師",
        reading_hira="はくいんぜんじ",
        reading_kana="ハクインゼンジ",
        source="llm",
    )

    merged = merge_channel_readings(channel, {entry.surface: entry})
    assert merged[entry.surface]["reading_kana"] == "ハクインゼンジ"

    loaded = load_channel_reading_dict(channel)
    assert loaded[entry.surface]["source"] == "llm"

    exported = export_words_for_word_dict(loaded)
    assert exported == {"白隠禅師": "ハクインゼンジ"}


def test_is_trivial_diff_filters_minor_variations():
    assert risk_utils.is_trivial_diff("こと", "コト")
    assert risk_utils.is_trivial_diff("コーヒー", "コヒー")
    assert risk_utils.is_trivial_diff("０．５", "0.5")
    assert not risk_utils.is_trivial_diff("怒り", "オコリ")


def test_collect_risky_candidates_skips_dict_entries():
    tokens = [
        RubyToken(surface="カルマ", reading_hira="かるま", token_index=0, line_id=1),
        RubyToken(surface="怒り", reading_hira="いかり", token_index=1, line_id=1),
    ]
    ruby_map = {1: "オコリ"}
    hazard_dict = {"怒り"}
    reading_dict = {"カルマ"}

    risky = risk_utils.collect_risky_candidates(tokens, ruby_map, hazard_dict, reading_dict)
    assert len(risky) == 1
    assert risky[0].token_index == 1
    assert risky[0].surface == "怒り"


def test_collect_risky_candidates_prioritizes_hazard_even_if_trivial():
    token = RubyToken(surface="AI", reading_hira="えーあい", token_index=0, line_id=0)
    ruby_map = {0: "エーアイ"}
    hazard_dict = {"AI"}
    risky = risk_utils.collect_risky_candidates([token], ruby_map, hazard_dict, reading_dict=set())
    assert len(risky) == 1
    assert risky[0].reason.startswith("hazard")
    assert risky[0].surface == "AI"


def test_group_risky_terms_limits_examples():
    spans = [
        RiskySpan(line_id=1, token_index=i, risk_score=1.0, reason="hazard:AI", surface="AI")
        for i in range(5)
    ]
    grouped = risk_utils.group_risky_terms(spans, max_examples=2)
    # single key, capped examples
    assert grouped[("AI", "hazard:AI")] == [0, 1]
