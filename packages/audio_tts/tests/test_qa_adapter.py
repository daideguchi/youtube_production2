import os

from audio_tts.tts.qa_adapter import qa_check


def test_qa_adapter_returns_issues():
    if not os.getenv("AZURE_OPENAI_API_KEY"):
        import pytest
        pytest.skip("AZURE_OPENAI_API_KEY not set")
    payload = {"a_text": "A", "b_text": "B", "b_text_build_log": []}
    res = qa_check(payload, model="gpt-5-mini", api_key=os.environ["AZURE_OPENAI_API_KEY"])
    assert "issues" in res
