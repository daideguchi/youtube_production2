from __future__ import annotations

from unittest.mock import patch

import pytest

from factory_common.codex_exec_layer import try_codex_exec


def test_tts_tasks_require_codex_exec_in_exec_slot_1(monkeypatch) -> None:
    # Bypass lockdown guards in unit tests.
    monkeypatch.setenv("YTM_EMERGENCY_OVERRIDE", "1")
    monkeypatch.setenv("YTM_CODEX_EXEC_ENABLE_IN_PYTEST", "1")
    monkeypatch.setenv("LLM_EXEC_SLOT", "1")

    cfg = {
        "enabled": True,
        "selection": {
            "include_task_prefixes": ["tts_"],
            "include_tasks": [],
            "exclude_tasks": [],
            "exclude_task_prefixes": [],
        },
    }

    with patch("factory_common.codex_exec_layer.subprocess.run", side_effect=FileNotFoundError("codex missing")):
        with pytest.raises(SystemExit) as excinfo:
            try_codex_exec(
                task="tts_reading",
                messages=[{"role": "user", "content": "hi"}],
                response_format="json_object",
                cfg=cfg,
            )

    msg = str(excinfo.value)
    assert "tts_*" in msg
    assert "LLM_EXEC_SLOT=2" in msg

