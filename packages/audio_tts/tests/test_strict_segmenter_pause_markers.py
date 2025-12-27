from audio_tts.tts.strict_segmenter import (
    PAUSE_H1,
    PAUSE_MARKER,
    PAUSE_PARAGRAPH,
    strict_segmentation,
)


def test_strict_segmentation_skips_pause_markers_and_blank_lines():
    text = "# タイトル\n\n本文1です。本文2です。\n---\n次の段落です。"
    segs = strict_segmentation(text)

    assert [s.text for s in segs] == ["タイトル", "本文1です。", "本文2です。", "次の段落です。"]
    assert segs[0].post_pause_sec == PAUSE_H1
    assert segs[2].post_pause_sec >= PAUSE_MARKER


def test_heading_boundary_inserts_paragraph_pause_before_heading():
    text = "前です。\n# 次\n後です。"
    segs = strict_segmentation(text)

    assert segs[0].post_pause_sec >= PAUSE_PARAGRAPH
