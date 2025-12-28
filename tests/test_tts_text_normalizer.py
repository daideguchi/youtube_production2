from audio_tts.tts.text_normalizer import normalize_text_for_tts


def test_normalize_text_for_tts_joins_middle_dots() -> None:
    assert normalize_text_for_tts("ニコラ・テスラ") == "ニコラテスラ"
    assert normalize_text_for_tts("レオナルド・ダ・ヴィンチ") == "レオナルドダヴィンチ"
    assert normalize_text_for_tts("レオナルド･ダ･ヴィンチ") == "レオナルドダヴィンチ"

    assert normalize_text_for_tts("あれ・これ") == "あれ・これ"
    assert normalize_text_for_tts("・項目") == "・項目"
    assert normalize_text_for_tts("A・Bテスト") == "ABテスト"

