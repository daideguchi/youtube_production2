import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from audio.tts import llm_adapter  # noqa: E402


def test_annotate_tokens_via_llm_monkeypatch(monkeypatch):
    def fake_run_llm_chat(model=None, messages=None, temperature=None, max_tokens=None, timeout=None):
        return {
            "content": {
                "token_annotations": [
                    {
                        "index": 0,
                        "surface": "今日",
                        "llm_reading_kana": "キョウ",
                        "write_mode": "hiragana",
                        "risk_level": 2,
                        "reason": "",
                        "reading_mecab": "キョウ",
                    }
                ]
            }
        }

    monkeypatch.setattr(llm_adapter, "run_llm_chat", fake_run_llm_chat)

    payload = {"original_text": "今日は", "tokens": [{"index": 0, "surface": "今日", "reading_mecab": "キョウ"}], "kana_engine_normalized": "キョウハ"}
    res = llm_adapter.annotate_tokens_via_llm(payload)
    assert res["token_annotations"][0]["write_mode"] == "hiragana"
