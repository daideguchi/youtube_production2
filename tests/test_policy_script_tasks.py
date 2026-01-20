from __future__ import annotations

from pathlib import Path

import pytest

from factory_common import agent_mode
from factory_common.codex_exec_layer import should_try_codex_exec


def test_script_tasks_blocked_in_think_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_EXEC_SLOT", "3")  # THINK
    monkeypatch.setenv("LLM_AGENT_QUEUE_DIR", str(tmp_path / "agent_queue"))

    messages = [{"role": "user", "content": "hi"}]
    options = {}
    with pytest.raises(SystemExit) as excinfo:
        agent_mode.maybe_handle_agent_mode(
            task="script_topic_research",
            messages=messages,
            options=options,
            response_format=None,
            return_raw=False,
        )

    msg = str(excinfo.value)
    assert "[THINK_MODE]" in msg
    task_id = agent_mode.compute_task_id("script_topic_research", messages, options)
    assert task_id in msg
    assert (tmp_path / "agent_queue" / "pending" / f"{task_id}.json").exists()


def test_codex_exec_never_applies_to_script_tasks(monkeypatch) -> None:
    # pytest safety: allow "should_try" evaluation (we still won't spawn the codex binary).
    # Also bypass lockdown guards (this is a unit test of hard policy behavior).
    monkeypatch.setenv("YTM_EMERGENCY_OVERRIDE", "1")
    monkeypatch.setenv("YTM_CODEX_EXEC_ENABLE_IN_PYTEST", "1")

    cfg = {
        "enabled": True,
        "selection": {
            "include_task_prefixes": ["script_", "tts_"],
            "include_tasks": [],
            "exclude_tasks": [],
            "exclude_task_prefixes": [],
        },
    }

    assert should_try_codex_exec("script_topic_research", cfg=cfg) is False
    assert should_try_codex_exec("script_chapter_draft", cfg=cfg) is False
    assert should_try_codex_exec("tts_annotate", cfg=cfg) is False
