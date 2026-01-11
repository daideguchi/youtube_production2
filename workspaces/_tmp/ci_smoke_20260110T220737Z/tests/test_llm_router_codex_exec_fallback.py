import os
import types
import unittest
from unittest.mock import patch

from factory_common.llm_router import LLMRouter


class DummyResponse:
    def __init__(self, content: str):
        self.choices = [
            types.SimpleNamespace(
                message=types.SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ]
        self.usage = types.SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3)


class DummyCompletions:
    def __init__(self, parent, content: str):
        self.parent = parent
        self.content = content

    def create(self, **kwargs):
        self.parent.last_params = kwargs
        self.parent.calls += 1
        return DummyResponse(self.content)


class DummyClient:
    def __init__(self, content: str = "ok"):
        self.calls = 0
        self.last_params = None
        self.chat = types.SimpleNamespace(completions=DummyCompletions(self, content))


class TestLLMRouterCodexExecFallback(unittest.TestCase):
    def setUp(self):
        # Unit tests should not depend on disk cache or logs.
        os.environ["LLM_API_CACHE_DISABLE"] = "1"
        os.environ["LLM_ROUTER_LOG_DISABLE"] = "1"
        os.environ.pop("LLM_FORCE_MODELS", None)
        os.environ.pop("LLM_FORCE_MODEL", None)
        os.environ.pop("LLM_FORCE_TASK_MODELS_JSON", None)
        # Reset singleton to avoid cross-test contamination.
        LLMRouter._instance = None

    def _make_router(self) -> tuple[LLMRouter, DummyClient]:
        router = LLMRouter()
        dummy = DummyClient(content="openrouter")

        # Override runtime state to avoid real HTTP.
        router.clients = {"openrouter": dummy}
        router.config = {
            "providers": {"openrouter": {"env_api_key": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1"}},
            "models": {
                "m_ok": {
                    "provider": "openrouter",
                    "model_name": "deepseek/deepseek-v3.2-exp",
                    "capabilities": {"mode": "chat", "reasoning": False, "json_mode": True, "max_tokens": 128},
                    "defaults": {"temperature": 0.2, "max_tokens": 32},
                }
            },
            "tiers": {"standard": ["m_ok"]},
            "tasks": {"general": {"tier": "standard", "options": {"max_tokens": 32}}},
        }
        router.task_overrides = {}
        return router, dummy

    def test_codex_exec_success_short_circuits(self):
        router, dummy = self._make_router()
        with patch(
            "factory_common.llm_router.try_codex_exec",
            return_value=("from_codex", {"attempted": True, "latency_ms": 12, "model": "gpt-5.2"}),
        ):
            out = router.call_with_raw("general", [{"role": "user", "content": "hi"}], max_tokens=16)

        self.assertEqual(out["content"], "from_codex")
        self.assertEqual(out["provider"], "codex_exec")
        self.assertEqual(out["chain"], ["codex_exec"])
        self.assertEqual(dummy.calls, 0)

    def test_codex_exec_failure_falls_back_to_api(self):
        router, dummy = self._make_router()
        with patch(
            "factory_common.llm_router.try_codex_exec",
            return_value=(None, {"attempted": True, "error": "codex_no_output"}),
        ):
            out = router.call_with_raw("general", [{"role": "user", "content": "hi"}], max_tokens=16)

        self.assertEqual(out["content"], "openrouter")
        self.assertEqual(out["provider"], "openrouter")
        self.assertEqual(out["chain"], ["m_ok"])
        self.assertGreaterEqual(dummy.calls, 1)

