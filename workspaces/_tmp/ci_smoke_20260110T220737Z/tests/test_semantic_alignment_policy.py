from __future__ import annotations

from packages.script_pipeline.runner import _semantic_alignment_is_pass, _truncate_for_semantic_check


def test_semantic_alignment_pass_policy_require_ok() -> None:
    assert _semantic_alignment_is_pass("ok", require_ok=True) is True
    assert _semantic_alignment_is_pass("minor", require_ok=True) is False
    assert _semantic_alignment_is_pass("major", require_ok=True) is False


def test_semantic_alignment_pass_policy_major_only() -> None:
    assert _semantic_alignment_is_pass("ok", require_ok=False) is True
    assert _semantic_alignment_is_pass("minor", require_ok=False) is True
    assert _semantic_alignment_is_pass("major", require_ok=False) is False


def test_truncate_for_semantic_check_no_truncate() -> None:
    out, meta = _truncate_for_semantic_check("abc", max_chars=10)
    assert out == "abc"
    assert meta["truncated"] is False


def test_truncate_for_semantic_check_truncates_and_bounds() -> None:
    out, meta = _truncate_for_semantic_check("a" * 100, max_chars=20)
    assert meta["truncated"] is True
    assert meta["char_count"] == 100
    assert len(out) <= 20

