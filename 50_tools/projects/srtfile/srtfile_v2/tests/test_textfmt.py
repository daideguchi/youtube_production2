import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.textfmt import (
    DELIM,
    MAX_LINE,
    MAX_LINES_PER_SECTION,
    Section,
    chunk_sentence_to_sections,
    normalize_block,
    parse_sections_from_text,
    render_sections,
    split_paragraphs,
    split_to_sentences,
    strip_list_prefix,
    wrap_text_to_lines,
)
from tools.textfmt.autofix_sections import sections_from_assembled, sections_from_final_script
from tools.textfmt.validate_sections import validate_script


def test_normalize_block_cleans_whitespace():
    text = "  テスト  文\u3000\n\n改行  "
    assert normalize_block(text) == "テスト 文 改行"


def test_split_to_sentences_handles_quotes():
    text = "「こんにちは。」彼は言った。驚きましたか？"
    sentences = split_to_sentences(text)
    assert sentences == ["「こんにちは。」", "彼は言った。", "驚きましたか？"]


def test_chunk_sentence_to_sections_produces_two_lines():
    sentence = "これはとても長い文章で二十七文字を大きく超えるため適切に折り返される必要があります。"
    sections, next_idx = chunk_sentence_to_sections(sentence, start_idx=5)
    assert next_idx > 5
    assert sections
    for sec in sections:
        assert 1 <= len(sec.lines) <= MAX_LINES_PER_SECTION
        for line in sec.lines:
            assert len(line) <= MAX_LINE


def test_sections_from_assembled_round_trip():
    assembled = (
        "第一章の導入です。ここでは要点を紹介します。\n\n"
        "第二章では具体的な解説をします。"
    )
    sections = sections_from_assembled(assembled, start_idx=1)
    rendered = render_sections(sections)
    ok, errs = validate_script(rendered)
    assert ok, errs


def test_parse_and_reformat_existing_final_script():
    scripted = "はじめまして。///\n今日は良い天気です。///"
    sections = parse_sections_from_text(scripted)
    assert sections == [["はじめまして。"], ["今日は良い天気です。"]]
    repaired = sections_from_final_script(scripted, start_idx=10)
    assert repaired[0].idx == 10
    assert render_sections(repaired).endswith("\n")


def test_validate_script_detects_errors():
    invalid = "テスト行です。\n改行のみ///\n未完の行\n"
    ok, errors = validate_script(invalid)
    assert not ok
    assert any("デリミタ" in e for e in errors)
