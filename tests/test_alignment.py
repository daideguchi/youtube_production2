from factory_common.alignment import alignment_suspect_reason


def test_alignment_suspect_reason_thumbnail_catch_mismatch() -> None:
    row = {
        "タイトル": "【東京】テスト",
        "サムネ画像プロンプト（URL・テキスト指示込み）": "『A』\nfoo",
        "DALL-Eプロンプト（URL・テキスト指示込み）": "『B』\nbar",
    }
    assert alignment_suspect_reason(row, "東京の話です") == "サムネプロンプト先頭行が不一致"


def test_alignment_suspect_reason_bracket_topic_missing() -> None:
    row = {"タイトル": "【東京】テスト"}
    reason = alignment_suspect_reason(row, "これは大阪の話です")
    assert isinstance(reason, str)
    assert reason.startswith("タイトル主要語が台本に出現しません")
    assert "(overlap=" in reason


def test_alignment_suspect_reason_ok() -> None:
    row = {"タイトル": "【東京】テスト"}
    assert alignment_suspect_reason(row, "東京の話です") is None

