import os
import types
import unittest

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
    def __init__(self, parent, fail_first: int, content: str):
        self.parent = parent
        self.fail_first = fail_first
        self.content = content

    def create(self, **kwargs):
        self.parent.last_params = kwargs
        self.parent.calls += 1
        if self.parent.calls <= self.fail_first:
            raise Exception("fail")
        return DummyResponse(self.content)


class DummyClient:
    def __init__(self, fail_first: int = 0, content: str = "ok"):
        self.calls = 0
        self.last_params = None
        self.chat = types.SimpleNamespace(completions=DummyCompletions(self, fail_first, content))


class TestLLMRouter(unittest.TestCase):
    def setUp(self):
        # Unit tests should not depend on or pollute disk cache.
        os.environ["LLM_API_CACHE_DISABLE"] = "1"
        os.environ.pop("LLM_FORCE_MODELS", None)
        os.environ.pop("LLM_FORCE_MODEL", None)
        os.environ.pop("LLM_FORCE_TASK_MODELS_JSON", None)
        # Reset singleton to avoid cross-test contamination.
        LLMRouter._instance = None

    def test_config_loading(self):
        router = LLMRouter()
        self.assertIn("providers", router.config)
        self.assertIn("models", router.config)
        self.assertIn("tiers", router.config)
        self.assertIn("tasks", router.config)

    def test_fallback_across_models(self):
        router = LLMRouter()
        dummy = DummyClient(fail_first=1, content="openrouter")

        # Override runtime state to avoid real HTTP.
        router.clients = {"openrouter": dummy}
        router.config = {
            "providers": {"openrouter": {"env_api_key": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1"}},
            "models": {
                "m_fail": {
                    "provider": "openrouter",
                    "model_name": "deepseek/deepseek-v3.2-exp",
                    "capabilities": {"mode": "chat", "reasoning": False, "json_mode": True, "max_tokens": 128},
                    "defaults": {"temperature": 0.2, "max_tokens": 32},
                },
                "m_ok": {
                    "provider": "openrouter",
                    "model_name": "deepseek/deepseek-v3.2-exp",
                    "capabilities": {"mode": "chat", "reasoning": False, "json_mode": True, "max_tokens": 128},
                    "defaults": {"temperature": 0.2, "max_tokens": 32},
                },
            },
            "tiers": {"standard": ["m_fail", "m_ok"]},
            "tasks": {"general": {"tier": "standard", "options": {"max_tokens": 32}}},
        }

        out = router.call_with_raw("general", [{"role": "user", "content": "hi"}], max_tokens=16)
        self.assertEqual(out["content"], "openrouter")
        self.assertEqual(out["chain"], ["m_fail", "m_ok"])
        self.assertGreaterEqual(dummy.calls, 2)

    def test_force_models_override(self):
        router = LLMRouter()
        router.config = {
            "providers": {"openrouter": {"env_api_key": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1"}},
            "models": {
                "m_a": {"provider": "openrouter", "model_name": "x", "capabilities": {"mode": "chat"}},
                "m_b": {"provider": "openrouter", "model_name": "y", "capabilities": {"mode": "chat"}},
            },
            "tiers": {"standard": ["m_a", "m_b"]},
            "tasks": {"general": {"tier": "standard"}},
        }
        router.task_overrides = {}
        os.environ["LLM_FORCE_MODELS"] = "m_b"
        self.assertEqual(router.get_models_for_task("general"), ["m_b"])

    def test_force_task_models_override_wins(self):
        router = LLMRouter()
        router.config = {
            "providers": {"openrouter": {"env_api_key": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1"}},
            "models": {
                "m_a": {"provider": "openrouter", "model_name": "x", "capabilities": {"mode": "chat"}},
                "m_b": {"provider": "openrouter", "model_name": "y", "capabilities": {"mode": "chat"}},
            },
            "tiers": {"standard": ["m_a"]},
            "tasks": {"general": {"tier": "standard"}, "other": {"tier": "standard"}},
        }
        router.task_overrides = {}
        os.environ["LLM_FORCE_MODELS"] = "m_a"
        os.environ["LLM_FORCE_TASK_MODELS_JSON"] = '{"general":["m_b"]}'
        self.assertEqual(router.get_models_for_task("general"), ["m_b"])
        self.assertEqual(router.get_models_for_task("other"), ["m_a"])

    def test_force_models_unknown_falls_back(self):
        router = LLMRouter()
        router.config = {
            "providers": {"openrouter": {"env_api_key": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1"}},
            "models": {
                "m_a": {"provider": "openrouter", "model_name": "x", "capabilities": {"mode": "chat"}},
            },
            "tiers": {"standard": ["m_a"]},
            "tasks": {"general": {"tier": "standard"}},
        }
        router.task_overrides = {}
        os.environ["LLM_FORCE_MODELS"] = "does_not_exist"
        self.assertEqual(router.get_models_for_task("general"), ["m_a"])

    def test_force_task_models_invalid_json_falls_back(self):
        router = LLMRouter()
        router.config = {
            "providers": {"openrouter": {"env_api_key": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1"}},
            "models": {
                "m_a": {"provider": "openrouter", "model_name": "x", "capabilities": {"mode": "chat"}},
            },
            "tiers": {"standard": ["m_a"]},
            "tasks": {"general": {"tier": "standard"}},
        }
        router.task_overrides = {}
        os.environ["LLM_FORCE_TASK_MODELS_JSON"] = "{"
        self.assertEqual(router.get_models_for_task("general"), ["m_a"])


if __name__ == "__main__":
    unittest.main()
