import pytest


def test_failover_skips_script_tasks(monkeypatch, tmp_path):
    import factory_common.llm_api_failover as f

    # Force failover-enabled context (lockdown → failover ON by default).
    monkeypatch.setenv("YTM_ROUTING_LOCKDOWN", "1")
    monkeypatch.delenv("YTM_EMERGENCY_OVERRIDE", raising=False)

    # Keep unit tests self-contained (no writes into repo logs).
    monkeypatch.setenv("LLM_AGENT_QUEUE_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_USAGE_LOG_DISABLE", "1")
    monkeypatch.setenv("LLM_FAILOVER_MEMO_DISABLE", "1")

    res = f.maybe_failover_to_think(
        task="script_chapter_draft",
        messages=[{"role": "user", "content": "hello"}],
        options={},
        response_format=None,
        return_raw=False,
        failure={"error": "provider_fail"},
    )
    assert res is None


def test_failover_non_script_creates_pending_and_stops(monkeypatch, tmp_path):
    import factory_common.llm_api_failover as f

    monkeypatch.setenv("YTM_ROUTING_LOCKDOWN", "1")
    monkeypatch.delenv("YTM_EMERGENCY_OVERRIDE", raising=False)

    monkeypatch.setenv("LLM_AGENT_QUEUE_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_USAGE_LOG_DISABLE", "1")
    monkeypatch.setenv("LLM_FAILOVER_MEMO_DISABLE", "1")

    with pytest.raises(SystemExit):
        f.maybe_failover_to_think(
            task="unit_test_task",
            messages=[{"role": "user", "content": "hello"}],
            options={},
            response_format=None,
            return_raw=False,
            failure={"error": "provider_fail"},
        )

    pending_dir = tmp_path / "pending"
    assert pending_dir.exists()
    assert list(pending_dir.glob("unit_test_task__*.json"))

