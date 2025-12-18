import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tts.qa_adapter import qa_check  # noqa: E402


def test_qa_adapter_returns_issues():
    if not os.getenv("AZURE_OPENAI_API_KEY"):
        import pytest
        pytest.skip("AZURE_OPENAI_API_KEY not set")
    payload = {"a_text": "A", "b_text": "B", "b_text_build_log": []}
    res = qa_check(payload, model="gpt-5-mini", api_key=os.environ["AZURE_OPENAI_API_KEY"])
    assert "issues" in res
