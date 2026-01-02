import pytest


def _make_router(monkeypatch, *, models):
    import factory_common.llm_router as lr

    # Reset singleton to avoid cross-test state.
    lr.LLMRouter._instance = None
    router = lr.LLMRouter()

    # Disable cache + agent-mode hooks for deterministic unit tests.
    monkeypatch.setattr(lr, "_api_cache_enabled_for_task", lambda _task: False)
    monkeypatch.setattr(lr, "maybe_handle_agent_mode", lambda **_kw: None)
    monkeypatch.setattr(lr, "sanitize_params", lambda _model_conf, opts: dict(opts))

    # Minimal model config + fake client.
    router.config = {
        "tasks": {},
        "models": {mk: {"provider": "dummy", "model_name": mk, "capabilities": {"mode": "chat"}, "defaults": {}} for mk in models},
        "tiers": {},
    }
    router.task_overrides = {}
    router.clients = {"dummy": object()}

    return router, lr


def test_strict_model_keys_disables_codex_and_think_and_fallback(monkeypatch):
    router, lr = _make_router(monkeypatch, models=["m1", "m2"])

    invoked = []
    codex_calls = {"n": 0}
    think_calls = {"n": 0}

    def _codex(**_kw):
        codex_calls["n"] += 1
        return None, {"attempted": True, "reason": "should_not_run"}

    monkeypatch.setattr(lr, "try_codex_exec", _codex)

    def _think_failover(**_kw):
        think_calls["n"] += 1
        return {"content": "THINK", "model": "think", "provider": "think", "chain": ["think"]}

    monkeypatch.setattr(lr, "maybe_failover_to_think", _think_failover)

    def _invoke(self, _provider, _client, _model_conf, _messages, return_raw=False, **_kwargs):
        invoked.append(_model_conf.get("model_name"))
        raise RuntimeError("provider_fail")

    monkeypatch.setattr(lr.LLMRouter, "_invoke_provider", _invoke, raising=True)

    with pytest.raises(RuntimeError):
        router.call_with_raw(
            task="unit_test_task",
            messages=[{"role": "user", "content": "hello"}],
            model_keys=["m1", "m2"],
        )

    # Strict-by-default: try ONLY the first model; do not call THINK failover.
    assert invoked == ["m1"]
    assert think_calls["n"] == 0
    assert codex_calls["n"] == 0


def test_allow_fallback_true_with_model_keys_tries_multiple_but_still_no_codex_or_think(monkeypatch):
    router, lr = _make_router(monkeypatch, models=["m1", "m2"])

    invoked = []
    codex_calls = {"n": 0}
    think_calls = {"n": 0}

    def _codex(**_kw):
        codex_calls["n"] += 1
        return None, {"attempted": True, "reason": "should_not_run"}

    monkeypatch.setattr(lr, "try_codex_exec", _codex)

    def _think_failover(**_kw):
        think_calls["n"] += 1
        return {"content": "THINK", "model": "think", "provider": "think", "chain": ["think"]}

    monkeypatch.setattr(lr, "maybe_failover_to_think", _think_failover)

    def _invoke(self, _provider, _client, _model_conf, _messages, return_raw=False, **_kwargs):
        invoked.append(_model_conf.get("model_name"))
        raise RuntimeError("provider_fail")

    monkeypatch.setattr(lr.LLMRouter, "_invoke_provider", _invoke, raising=True)

    with pytest.raises(RuntimeError):
        router.call_with_raw(
            task="unit_test_task",
            messages=[{"role": "user", "content": "hello"}],
            model_keys=["m1", "m2"],
            allow_fallback=True,
        )

    assert invoked == ["m1", "m2"]
    assert think_calls["n"] == 0
    assert codex_calls["n"] == 0
