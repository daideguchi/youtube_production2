import pytest

from commentary_01_srtfile_v2.core import llm_rewriter


class TestExtractOpenRouterMessageText:
    def test_plain_string_content(self):
        choices = [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "これはプレーンなテキストです。",
                },
            }
        ]
        text = llm_rewriter._extract_openrouter_message_text(choices)
        assert text == "これはプレーンなテキストです。"

    def test_list_content_with_text_parts(self):
        choices = [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "前半です。"},
                        {"type": "text", "text": "後半です。"},
                        {"type": "reasoning", "text": "これは使わない。"},
                    ],
                },
            }
        ]
        text = llm_rewriter._extract_openrouter_message_text(choices)
        assert text == "前半です。後半です。"

    def test_fallback_to_reasoning_when_content_empty(self):
        choices = [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning": "これは reasoning だけ入っているケースです。",
                },
            }
        ]
        text = llm_rewriter._extract_openrouter_message_text(choices)
        assert text == "これは reasoning だけ入っているケースです。"

    def test_raises_when_both_empty(self):
        choices = [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning": None,
                },
            }
        ]
        with pytest.raises(RuntimeError) as excinfo:
            llm_rewriter._extract_openrouter_message_text(choices)
        assert "content を取得できません" in str(excinfo.value)
