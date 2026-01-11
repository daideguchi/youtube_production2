from audio_tts.tts.reading_dict import is_banned_surface, export_words_for_word_dict


def test_is_banned_surface():
    assert is_banned_surface("今日") is True
    assert is_banned_surface("今") is True
    assert is_banned_surface("一行") is True
    assert is_banned_surface("") is True
    assert is_banned_surface("あ") is True  # 1文字
    assert is_banned_surface("漢字語") is False


def test_export_words_filters_banned_surfaces():
    data = {
        "今日": {"reading_kana": "キョウ"},
        "安全": {"reading_kana": "アンゼン"},
        "今": {"reading_kana": "イマ"},
    }
    out = export_words_for_word_dict(data)
    assert "安全" in out
    assert "今日" not in out
    assert "今" not in out
