from __future__ import annotations

from packages.script_pipeline.runner import _should_skip_script_validation_llm_gate


def test_skip_llm_gate_unchanged_input() -> None:
    skip, reason, detail = _should_skip_script_validation_llm_gate(
        llm_gate_enabled=True,
        force_llm_gate=False,
        prev_verdict="pass",
        prev_input_fingerprint="abc",
        current_input_fingerprint="abc",
        char_count=1000,
        max_a_text_chars=30000,
    )
    assert skip is True
    assert reason == "unchanged_input"
    assert detail == {}


def test_skip_llm_gate_too_long() -> None:
    skip, reason, detail = _should_skip_script_validation_llm_gate(
        llm_gate_enabled=True,
        force_llm_gate=False,
        prev_verdict="fail",
        prev_input_fingerprint="abc",
        current_input_fingerprint="def",
        char_count=40000,
        max_a_text_chars=30000,
    )
    assert skip is True
    assert reason == "too_long"
    assert detail["char_count"] == 40000
    assert detail["max_a_text_chars"] == 30000
    assert detail["env"] == "SCRIPT_VALIDATION_LLM_MAX_A_TEXT_CHARS"


def test_skip_llm_gate_force_override() -> None:
    skip, reason, detail = _should_skip_script_validation_llm_gate(
        llm_gate_enabled=True,
        force_llm_gate=True,
        prev_verdict="pass",
        prev_input_fingerprint="abc",
        current_input_fingerprint="abc",
        char_count=999999,
        max_a_text_chars=1,
    )
    assert skip is False
    assert reason is None
    assert detail == {}


def test_skip_llm_gate_disabled() -> None:
    skip, reason, detail = _should_skip_script_validation_llm_gate(
        llm_gate_enabled=False,
        force_llm_gate=False,
        prev_verdict="pass",
        prev_input_fingerprint="abc",
        current_input_fingerprint="abc",
        char_count=999999,
        max_a_text_chars=1,
    )
    assert skip is False
    assert reason is None
    assert detail == {}

