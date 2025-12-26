import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tts import llm_adapter  # noqa: E402


def test_llm_adapter_monkeypatch():
    if not os.getenv("AZURE_OPENAI_API_KEY"):
        import pytest
        pytest.skip("AZURE_OPENAI_API_KEY not set")
    payload = {
        "original_text": "今日は",
        "tokens": [{"index": 0, "surface": "今日", "reading_mecab": "キョウ"}],
        "kana_engine_normalized": "キョウハ",
    }
    res = llm_adapter.annotate_tokens(payload, model="gpt-5-mini", api_key=os.environ["AZURE_OPENAI_API_KEY"])
    assert "token_annotations" in res
