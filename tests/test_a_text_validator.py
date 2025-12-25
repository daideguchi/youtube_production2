from script_pipeline.validator import validate_a_text


def test_validate_a_text_rejects_percent_sign() -> None:
    issues, _stats = validate_a_text("今日は10%だけ楽になります。", {})
    codes = {item.get("code") for item in issues}
    assert "forbidden_statistics" in codes


def test_validate_a_text_rejects_percent_word() -> None:
    issues, _stats = validate_a_text("成功率は五十パーセントです。", {})
    codes = {item.get("code") for item in issues}
    assert "forbidden_statistics" in codes


def test_validate_a_text_allows_normal_text() -> None:
    issues, _stats = validate_a_text("今日は少し呼吸を整えてみましょう。", {})
    codes = {item.get("code") for item in issues}
    assert "forbidden_statistics" not in codes


def test_validate_a_text_rejects_incomplete_ending() -> None:
    issues, _stats = validate_a_text("こうした習慣は、日常の助けになり", {})
    codes = {item.get("code") for item in issues}
    assert "incomplete_ending" in codes


def test_validate_a_text_allows_trailing_pause_lines_after_complete_sentence() -> None:
    issues, _stats = validate_a_text("今日は少し呼吸を整えてみましょう。\n\n---\n", {})
    codes = {item.get("code") for item in issues}
    assert "incomplete_ending" not in codes
