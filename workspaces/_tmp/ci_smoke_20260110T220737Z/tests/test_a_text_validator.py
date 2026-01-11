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


def test_validate_a_text_rejects_duplicate_paragraph() -> None:
    para = (
        "相手の言葉に反射的に反応する前に、一呼吸置いてください。"
        "息を吸って吐く間に、怒りや悲しみを感じ取り、事実に戻して言葉を選び直す。"
        "正しさで刺すより、関係を続けるための言葉を選ぶ。"
        "無理な要求には、できませんと短く境界線を示す。"
        "それだけで縁は守られます。"
    )
    issues, _stats = validate_a_text(f"{para}\n\n{para}\n", {})
    codes = {item.get("code") for item in issues}
    assert "duplicate_paragraph" in codes


def test_validate_a_text_rejects_replacement_character() -> None:
    issues, _stats = validate_a_text("今日は�です。", {})
    codes = {item.get("code") for item in issues}
    assert "replacement_character" in codes


def test_validate_a_text_rejects_control_or_format_characters() -> None:
    issues, _stats = validate_a_text("今日は\u200b少し呼吸を整えてみましょう。", {})
    codes = {item.get("code") for item in issues}
    assert "forbidden_unicode_control" in codes


def test_validate_a_text_rejects_suspicious_glyph() -> None:
    issues, _stats = validate_a_text("尊厊が守られていると感じたなら。", {})
    codes = {item.get("code") for item in issues}
    assert "suspicious_glyph" in codes
