import pytest

from commentary_01_srtfile_v2.core import llm_rewriter


class TestValidateOutput:
    def test_ok_normal_japanese_output(self):
        original = "2022年の調査では70%のキリスト教研究者が公開を支持しており、その影響は現代にも及んでいます。"
        rewritten = (
            "2022年の調査では、70パーセントのキリスト教研究者が、公開を支持しており、"
            "その影響は、現代にも及んでいます。"
        )
        llm_rewriter._validate_output(original, rewritten)

    def test_too_short_output_raises(self):
        original = "これはかなり長めの元テキストで、情報量もそれなりに含まれています。"
        rewritten = "短すぎ"
        with pytest.raises(RuntimeError) as excinfo:
            llm_rewriter._validate_output(original, rewritten)
        assert "短すぎ" in str(excinfo.value)

    def test_empty_output_raises(self):
        original = "元テキストがあります。"
        rewritten = ""
        with pytest.raises(RuntimeError) as excinfo:
            llm_rewriter._validate_output(original, rewritten)
        assert "短すぎ" in str(excinfo.value)

    def test_codeblock_is_rejected(self):
        original = "元テキスト。"
        rewritten = """ここからは説明です。

```json
{"foo": "bar"}
```"""
        with pytest.raises(RuntimeError) as excinfo:
            llm_rewriter._validate_output(original, rewritten)
        assert "メタ情報" in str(excinfo.value) or "コード" in str(excinfo.value)

    def test_analysis_reasoning_meta_is_rejected(self):
        original = "2022年の調査では70%のキリスト教研究者が公開を支持しています。"
        rewritten = (
            "Analysis: まずこの文章をどのように変換するか考えます。\n"
            "Reasoning: ユーザーは……"
        )
        with pytest.raises(RuntimeError) as excinfo:
            llm_rewriter._validate_output(original, rewritten)
        msg = str(excinfo.value).lower()
        assert "メタ" in msg or "llm 出力" in msg

    def test_too_many_newlines_is_rejected(self):
        original = "これは普通の1行テキストです。"
        rewritten = "\n" * 15
        with pytest.raises(RuntimeError) as excinfo:
            llm_rewriter._validate_output(original, rewritten)
        msg = str(excinfo.value)
        # 短すぎ判定か改行だらけ判定のどちらかで落ちればOK
        assert ("改行" in msg) or ("短すぎ" in msg)
