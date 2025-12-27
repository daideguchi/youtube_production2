from audio_tts.tts.builder import build_b_text, chunk_b_text


def test_build_b_text_hiragana():
    a_text = "今日は東京"
    tokens = [
        {"index": 0, "surface": "今日", "char_start": 0, "char_end": 2},
        {"index": 1, "surface": "は", "char_start": 2, "char_end": 3},
    ]
    anns = [
        {"index": 0, "write_mode": "hiragana", "llm_reading_kana": "キョウ"},
        {"index": 1, "write_mode": "original"},
    ]
    b_text, log = build_b_text(a_text, tokens, anns)
    assert b_text.startswith("きょう")
    assert log[0]["replaced_fragment"] == "きょう"


def test_chunk_b_text():
    text = "これはテストです。次の文です。さらにもう一文足します。"
    chunks = chunk_b_text(text, max_len=20)
    assert all(len(c["text"]) <= 20 for c in chunks)
