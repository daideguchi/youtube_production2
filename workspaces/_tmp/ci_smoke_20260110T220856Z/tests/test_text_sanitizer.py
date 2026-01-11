from __future__ import annotations

from factory_common.text_sanitizer import strip_meta_from_script


def test_strip_meta_removes_markdown_ref_citation() -> None:
    src = "本文です。([戦国ヒストリー][13])\n"
    res = strip_meta_from_script(src)
    assert "戦国ヒストリー" not in res.text
    assert "[13]" not in res.text
    assert res.text == "本文です。\n"
    assert res.removed_counts.get("md_ref_paren", 0) == 1


def test_strip_meta_removes_bare_numeric_footnote_two_digits_only() -> None:
    src = "A[13] B[1]\n"
    res = strip_meta_from_script(src)
    assert "[13]" not in res.text
    # Keep single-digit bracket tokens (may be pause tags in some flows)
    assert "[1]" in res.text


def test_strip_meta_removes_urls() -> None:
    src = "参考: https://example.com/test?x=1\nwww.example.com/abc\n"
    res = strip_meta_from_script(src)
    assert "http" not in res.text
    assert "www.example.com" not in res.text
    assert res.removed_counts.get("url", 0) == 1
    assert res.removed_counts.get("www", 0) == 1


def test_strip_meta_removes_markdown_ref_def_lines() -> None:
    src = "本文\n\n[13]: https://example.com\n[foo]: https://example.com/x\n"
    res = strip_meta_from_script(src)
    assert "https://example.com" not in res.text
    assert "[13]:" not in res.text
    assert "[foo]:" not in res.text
    assert res.removed_counts.get("md_ref_def", 0) == 2

