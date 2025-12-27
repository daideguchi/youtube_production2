from script_pipeline.runner import _sanitize_inline_pause_markers


def test_sanitize_inline_pause_markers_splits_inline_markers() -> None:
    out = _sanitize_inline_pause_markers("A --- B\n")
    assert out == "A\n---\nB\n"


def test_sanitize_inline_pause_markers_keeps_standalone_marker() -> None:
    text = "A\n---\nB\n"
    out = _sanitize_inline_pause_markers(text)
    assert out == text


def test_sanitize_inline_pause_markers_normalizes_hyphen_run_line() -> None:
    out = _sanitize_inline_pause_markers("----\n")
    assert out == "---\n"

