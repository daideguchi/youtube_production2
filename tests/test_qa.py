import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from audio.tts.qa import build_qa_payload, validate_qa_response  # noqa: E402


def test_build_qa_payload_fields():
    payload = build_qa_payload("A", "B", [{"index": 0, "original_fragment": "X"}])
    assert payload["a_text"] == "A"
    assert payload["b_text"] == "B"
    assert payload["b_text_build_log"][0]["original_fragment"] == "X"


def test_validate_qa_response_accepts_issues():
    resp = {"issues": [{"index": 1, "suggestion": "ひらがなにする"}]}
    issues = validate_qa_response(resp)
    assert issues[0]["index"] == 1
    assert "ひらがな" in issues[0]["suggestion"]


def test_validate_qa_response_requires_index():
    bad = {"issues": [{}]}
    try:
        validate_qa_response(bad)
    except ValueError as exc:
        assert "index" in str(exc)
