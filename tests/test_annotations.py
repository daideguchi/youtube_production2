import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from audio.tts.annotations import build_prompt_payload, validate_llm_response  # noqa: E402


def test_build_prompt_payload_contains_fields():
    payload = build_prompt_payload(
        original_text="今日はテストです。",
        tokens=[{"index": 0, "surface": "今日"}],
        kana_engine_normalized="キョウハテストデス",
    )
    assert payload["original_text"].startswith("今日")
    assert payload["tokens"][0]["surface"] == "今日"
    assert payload["kana_engine_normalized"] == "キョウハテストデス"
    assert payload["risk_dictionary"] == []


def test_validate_llm_response_happy_path():
    response = {
        "token_annotations": [
            {
                "index": 0,
                "surface": "今日",
                "llm_reading_kana": "キョウ",
                "write_mode": "hiragana",
                "risk_level": 3,
                "reason": "誤読リスク",
            }
        ]
    }
    anns = validate_llm_response(response)
    assert anns[0]["index"] == 0
    assert anns[0]["write_mode"] == "hiragana"
    assert "reading_mecab" in anns[0]


def test_validate_llm_response_missing_index_raises():
    bad = {"token_annotations": [{}]}
    try:
        validate_llm_response(bad)
    except ValueError as exc:
        assert "index" in str(exc)
